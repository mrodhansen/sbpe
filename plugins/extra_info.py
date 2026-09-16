import logging
import math
import platform
import time

from _remote import ffi, lib
from manager import PluginBase
import util


def _view_offset(wv):
    fn = getattr(util, 'view_offset', None)
    if fn is not None:
        return fn(wv)
    if platform.system() == 'Darwin':
        xy = ffi.cast('int32_t *', int(ffi.cast('uintptr_t', wv)) + 0xbc)
        return int(xy[0]), int(xy[1])
    return int(wv.offset.x), int(wv.offset.y)


TARGETS = {
    'door': '''
        cave.nextDoor cave.bossgate
        city.nextDoor city.bossgate
        dumb.nextDoor dumb.bossgate
        forest.nextdoor forest.unextdoor forest.bossgate
        hulk.door3 hulk.prevdoor hulk.hportalbase
        ice.nextDoor ice.bossgate
        jungle.door jungle.bossgate
        lab.nextdoor lab.bossgate
        sc.door
    ''',

    'shop': '''
        dumb.secretspot dumb.secretspoton
        hulk.sspot hulk.sdopen
        jungle.sspot jungle.sdopen
        lab.sspot lab.sspotdone
    ''',

    'secret': '''
        cave.sd
        city.sd
        dumb.fw
        forest.sspot forest.sdopen
        hulk.sd2
        jungle.sd2
        lab.sd2
    ''',

    'arcade': '''
        forest.miner
        dumb.steletop
        hulk.sg
        lab.sg
    ''',

    'player': 'soldier assassin heavy fabricator toaster',

    'hp': 'loot-health1 loot-xmas',

    'boost': ''
}

TVIDMAP = {}

for k in TARGETS:
    klist = TARGETS[k].split()
    for vid in klist:
        TVIDMAP[vid] = k

BOOSTS = {
    # vid prefix : (PlayerCharacter StatVal field, CharacterDescription list)
    'loot-maxhealth': ('maxHitpoints', 'maxhitpoints'),
    'loot-maxammo': ('maxAmmo', 'maxammo'),
    'loot-damage': ('damageBonus', 'damagebonus'),
    'loot-armor': ('armor', 'armor'),
    'loot-mspeed': ('walkSpeed', 'walkspeed'),
    'loot-jheight': ('jumpSpeed', 'jumpspeed'),
    'loot-critchance': ('critChance', 'critchance'),
    'loot-critmult': ('critMult', 'critmult'),
}

# Mac offsets from PlayerCharacter::mk in mvmmoclient (CFFI Player is short).
_MAC_PC_CHARDESC = 0x278
_MAC_PC_STAT_BASE = {
    'maxHitpoints': 0x2c4,
    'maxAmmo': 0x2cc,
    'damageBonus': 0x2dc,
    'armor': 0x2e4,
    'walkSpeed': 0x2ec,
    'jumpSpeed': 0x2f4,
    'critChance': 0x2fc,
    'critMult': 0x304,
}
_MAC_DESC_REPEATED = {
    'maxhitpoints': 0x28,
    'maxammo': 0x38,
    'damagebonus': 0x48,
    'armor': 0x60,
    'walkspeed': 0x70,
    'jumpspeed': 0x80,
    'critchance': 0x90,
    'critmult': 0xa0,
}


def _boost_key(vid):
    if not vid:
        return None, 0
    if vid in BOOSTS:
        return vid, 1
    if vid[-1].isdigit() and vid[:-1] in BOOSTS:
        return vid[:-1], int(vid[-1])
    return None, 0


def _as_pc(plr):
    if plr == ffi.NULL:
        return ffi.NULL
    return ffi.cast('struct PlayerCharacter *', plr)


def _i32_at(obj, off):
    return int(ffi.cast('int32_t *', int(ffi.cast('uintptr_t', obj)) + off)[0])


def _ptr_at(obj, off):
    return ffi.cast('void **', int(ffi.cast('uintptr_t', obj)) + off)[0]


def _repeated_ints(desc, field_off):
    base = int(ffi.cast('uintptr_t', desc)) + field_off
    raw = ffi.cast('void *', base)
    if not util._ptr_mapped(raw, 16):
        return None
    elems = ffi.cast('int32_t **', raw)[0]
    n = int(ffi.cast('int32_t *', base + 8)[0])
    if n <= 0 or n > 32 or elems == ffi.NULL:
        return None
    if not util._ptr_mapped(elems, n * 4):
        return None
    return list(ffi.unpack(elems, n))


def _boost_state(plr, bkey):
    '''(current base, upgrade table) for a boost vid prefix.'''
    field, desc_field = BOOSTS[bkey]
    if platform.system() == 'Darwin':
        if not util._ptr_mapped(plr, 0x310):
            return None, None
        curr = _i32_at(plr, _MAC_PC_STAT_BASE[field])
        desc = _ptr_at(plr, _MAC_PC_CHARDESC)
        if desc == ffi.NULL or not util._ptr_mapped(desc, 0xb0):
            return curr, None
        return curr, _repeated_ints(desc, _MAC_DESC_REPEATED[desc_field])
    pc = _as_pc(plr)
    curr = int(getattr(pc, field).base)
    desc = pc.charDesc
    if desc == ffi.NULL:
        return curr, None
    mfield = getattr(desc, desc_field)
    n = int(mfield.current_size)
    if n <= 0 or n > 32 or mfield.elements == ffi.NULL:
        return curr, None
    return curr, list(ffi.unpack(mfield.elements, n))


def _boost_needed(plr, bkey, blevel):
    curr, mvals = _boost_state(plr, bkey)
    if mvals is None or blevel < 1:
        return False, curr, mvals
    idx = blevel if blevel < len(mvals) else len(mvals) - 1
    if idx < 1:
        return False, curr, mvals
    return mvals[idx] > curr, curr, mvals

OPTS = (
    ('color', 0xffffffff, 'color'),
    ('length', 20, 'float'),
    ('width', 10, 'float'),
    ('outline', 2, 'float'),
    ('fade', 300, 'int'),
    ('frame', 5, 'float'),
    ('blink', 0, 'float')
)


def rotate(x, y, angle):
    rx = x * math.cos(angle) - y * math.sin(angle)
    ry = x * math.sin(angle) + y * math.cos(angle)
    return (rx, ry)


class Plugin(PluginBase):
    def onInit(self):
        self.config.option('trigger_color', 0xffff00ff, 'color')
        self.config.option('trigger_frame', 3, 'int')

        self.config.option('arrows', '', 'str')

        for opt in OPTS:
            self.config.option('arrows_' + opt[0], opt[1], opt[2])

        self.config.option('show_hp', False, 'bool')
        self.config.option('show_uses', True, 'bool')
        self.config.option('show_room_id', True, 'bool')

        self._initedopts = False
        self.numbers = util.NumberDict(size=16, color=0xffff80, font=b'TenbyFive')
        self.negnumbers = util.NumberDict(size=16, color=0x8080ff, font=b'TenbyFive')
        self.roomtxt = util.PlainText(size=16, font=b'TenbyFive')

    def onPresent(self):
        if not self._initedopts:
            self._initedopts = True
            for k in TARGETS.keys():
                for opt in OPTS:
                    self.config.option(
                        'arrow_{}_{}'.format(k, opt[0]),
                        self.config['arrows_' + opt[0]], opt[2])

        cw = self.refs.ClientWorld
        wv = self.refs.WorldView
        if cw == ffi.NULL or wv == ffi.NULL:
            return

        objects = util.collect_objects(cw)
        plr = util.find_player(cw)
        if plr == ffi.NULL:
            plr = self.refs.player if util.player_looks_alive(self.refs.player) else ffi.NULL
        pc = _as_pc(plr) if util.player_looks_alive(plr) else ffi.NULL
        if pc == ffi.NULL:
            plr = ffi.NULL
        ox, oy = _view_offset(wv)
        pclass = 'ok' if plr != ffi.NULL else 'NULL'
        now = time.perf_counter()
        if not getattr(self, '_logged', False) or now - getattr(self, '_lastlog', 0) > 5:
            self._logged = True
            self._lastlog = now
            self._need_counts = True
            samples = []
            loot = []
            for obj in objects:
                try:
                    vid = util.getstr(obj.props.vid)
                except Exception as exc:
                    samples.append('err:{}'.format(exc))
                    continue
                if len(samples) < 12:
                    samples.append(vid)
                if vid.startswith('loot-') or vid in TVIDMAP or _boost_key(vid)[0]:
                    loot.append(vid)
            php = px = py = None
            if plr != ffi.NULL:
                try:
                    pr = ffi.cast('struct WorldObject *', plr).props
                    php, px, py = int(pr.hitpoints), int(pr.xmp), int(pr.ymp)
                except Exception as exc:
                    samples.append('plr_props:{}'.format(exc))
            logging.info(
                'extra_info plr=%s pclass=%s hp=%s xmp=%s ymp=%s '
                'objs=%d offset=%d,%d arrows=%r show_hp=%s loot=%s samples=%s',
                plr != ffi.NULL, pclass, php, px, py,
                len(objects), ox, oy, self.config.arrows,
                self.config.show_hp, loot[:20], samples)

        # UNKNOWN class still has a usable WorldObject; do not abort.
        kinds = self.config.arrows.split()
        n_vid = n_arrow = n_hp = n_use = n_trig = 0
        n_kind = {}

        for obj in objects:
            try:
                p = obj.props
                vid = util.getstr(p.vid)
            except Exception:
                continue
            if vid and vid != '(NULL)':
                n_vid += 1

            # invisible
            if len(vid) == 0 or vid == '(NULL)':
                # triggers
                if p.trigger != 0:
                    n_trig += 1
                    self.drawFrame(
                        obj, self.config.trigger_color,
                        self.config.trigger_frame)
                # otherwise skip
                continue

            w2 = p.wmp // 512
            h2 = p.hmp // 512
            x = p.xmp // 256 + w2 - ox
            y = p.ymp // 256 + h2 - oy
            inbounds = x + w2 > 0 and y + h2 > 0 and\
                x - w2 < self.refs.canvasW_[0] and\
                y - h2 < self.refs.canvasH_[0]

            # object hp/armor — skip walls/garbage (hitpoints often 0 or huge)
            if inbounds and self.config.show_hp:
                hp = int(p.hitpoints)
                if 0 <= hp <= 9999:
                    n_hp += 1
                    self.numbers.draw(hp, x, y, anchorX=0.5, anchorY=1)
                elif -9999 <= hp <= -2:
                    n_hp += 1
                    self.negnumbers.draw(-hp, x, y, anchorX=0.5, anchorY=1)
                armor = int(p.armor)
                if 0 < armor <= 9999:
                    self.numbers.draw(armor, x, y, anchorX=0.5, anchorY=0)

            # use counts
            if inbounds and self.config.show_uses and p.interact != 0:
                idesc = p.interactdescription
                if idesc != ffi.NULL and util._ptr_mapped(idesc, 16) and \
                        idesc.numused > 0 and idesc.totaluses > 0:
                    n_use += 1
                    self.numbers.draw(
                        idesc.numused, x, y, anchorX=0.5, anchorY=0.5)

            # boosts — only if this drop upgrades a stat we don't already have
            bkey, blevel = _boost_key(vid)
            if 'boost' in kinds and bkey is not None:
                if plr != ffi.NULL:
                    try:
                        need, curr, mvals = _boost_needed(plr, bkey, blevel)
                        if getattr(self, '_need_counts', False):
                            logging.info(
                                'boost %s lv=%s need=%s curr=%s table=%s',
                                vid, blevel, need, curr, mvals)
                        if need:
                            n_arrow += 1
                            n_kind['boost'] = n_kind.get('boost', 0) + 1
                            self.drawArrow(plr, obj, 'boost')
                    except Exception:
                        logging.exception('boost %s', vid)
                continue

            # match vid name with target list
            try:
                k = TVIDMAP[vid]
            except KeyError:
                continue

            # consider only enabled types
            if k not in kinds:
                continue

            # do we need hp?
            if k == 'hp' and plr != ffi.NULL:
                plrprops = ffi.cast('struct WorldObject *', plr).props
                if plrprops.hitpoints == plrprops.maxhitpoints:
                    continue

            # all checks passed, we are interested in this obj
            if plr == ffi.NULL:
                continue
            n_arrow += 1
            n_kind[k] = n_kind.get(k, 0) + 1
            self.drawArrow(plr, obj, k)

        if getattr(self, '_need_counts', False):
            self._need_counts = False
            logging.info(
                'extra_info counts vid=%d hpdraw=%d uses=%d trig=%d arrows=%d by_kind=%s',
                n_vid, n_hp, n_use, n_trig, n_arrow, n_kind)

        # zone/room id
        if self.config.show_room_id:
            cwprops = cw.asWorld.props
            txt = util.getstr(cwprops.zone) or util.getstr(cwprops.music)
            if cwprops.floor > 0:
                txt = '{} {}'.format(txt, cwprops.floor + 1)
            self.roomtxt.text = txt
            self.roomtxt.draw(
                self.refs.windowW - 4, self.refs.windowH - 4,
                anchorX=1, anchorY=1)

    def drawArrow(self, src, dst, kind):
        optprefix = 'arrow_' + kind + '_'

        ox, oy = _view_offset(self.refs.WorldView)
        cw = self.refs.canvasW_[0]
        ch = self.refs.canvasH_[0]

        sp = ffi.cast('struct WorldObject *', src).props
        dp = ffi.cast('struct WorldObject *', dst).props

        dw2 = dp.wmp // 512
        dh2 = dp.hmp // 512
        x1 = (dp.xmp) // 256 + dw2 - ox
        y1 = (dp.ymp) // 256 + dh2 - oy

        inbounds = (x1 + dw2 >= 0 and x1 - dw2 <= cw and y1 + dh2 >= 0 and y1 - dh2 <= ch)

        if inbounds and self.config[optprefix + 'frame'] == 0:
            return

        if inbounds:
            x0 = (sp.xmp + sp.wmp // 2) // 256 - ox
            y0 = (sp.ymp + sp.hmp // 2) // 256 - oy
        else:
            x0 = cw // 2
            y0 = ch // 2

        angle = math.atan2(y1 - y0, x1 - x0)

        # offset target position to the canvas edges if out of bounds
        t = 1
        if x1 < 0 and x0 != x1:
            t = min(t, x0 / (x0 - x1))
        if y1 < 0 and y0 != y1:
            t = min(t, y0 / (y0 - y1))
        if x1 > cw and x0 != x1:
            t = min(t, (cw - x0) / (x1 - x0))
        if y1 > ch and y0 != y1:
            t = min(t, (ch - y0) / (y1 - y0))
        # arrow point coords
        ax = x0 + t * (x1 - x0)
        ay = y0 + t * (y1 - y0)

        # fading
        dist = math.sqrt((ax - x0) ** 2 + (ay - y0) ** 2)
        fade = self.config[optprefix + 'fade']
        color = self.config[optprefix + 'color']
        f = 1
        if dist < fade and fade > 0:
            f = dist / fade
        # blinking
        bp = self.config[optprefix + 'blink']
        if bp > 0:
            f = f * abs(time.perf_counter() % bp - bp / 2) * 2 / bp

        if f < 1:
            ca = math.floor((color >> 24) * f) & 0xff
            color = (color & 0xffffff) | (ca << 24)

        # draw frame
        if inbounds:
            self.drawFrame(dst, color, self.config[optprefix + 'frame'])
            return

        # draw triangle
        dx = self.config[optprefix + 'length'] * self.refs.scaleX
        dy = self.config[optprefix + 'width'] / 2 * self.refs.scaleX

        # outline
        out = self.config[optprefix + 'outline'] * self.refs.scaleX
        if out > 0:
            offx = out / math.tan(math.pi / 4 - math.atan2(dx, dy) / 2)
            offy = out / math.tan(math.pi / 4 - math.atan2(dy, dx) / 2)
            (oax, oay) = rotate(offx, 0, angle)
            (obx, oby) = rotate(-dx - out, dy + offy, angle)
            (ocx, ocy) = rotate(-dx - out, -dy - offy, angle)
            self.refs.XDL_FillTri(
                round(ax + oax), round(ay + oay),
                round(ax + obx), round(ay + oby),
                round(ax + ocx), round(ay + ocy),
                color & 0xff000000, lib.BLENDMODE_BLEND)

        # middle
        (bx, by) = rotate(-dx, dy, angle)
        (cx, cy) = rotate(-dx, -dy, angle)
        self.refs.XDL_FillTri(
            round(ax), round(ay),
            round(ax + bx), round(ay + by),
            round(ax + cx), round(ay + cy),
            color, lib.BLENDMODE_BLEND)

    def drawFrame(self, obj, color, width):
        if color == 0 or width <= 0:
            return

        blend = lib.BLENDMODE_ADD
        p = obj.props
        ox, oy = _view_offset(self.refs.WorldView)
        x = p.xmp // 256 - ox
        y = p.ymp // 256 - oy
        w = p.wmp // 256
        h = p.hmp // 256

        cw = self.refs.canvasW_[0]
        ch = self.refs.canvasH_[0]
        if x + w < 0 or y + h < 0 or x > cw or y > ch:
            return

        lw = max(1, round(width * self.refs.scaleX))
        if lw * 2 > w or lw * 2 > h:
            self.refs.XDL_FillRect(x, y, w, h, color, blend)
            return

        self.refs.XDL_FillRect(x, y, w - lw, lw, color, blend)
        self.refs.XDL_FillRect(x + lw, y + h - lw, w - lw, lw, color, blend)

        self.refs.XDL_FillRect(x, y + lw, lw, h - lw, color, blend)
        self.refs.XDL_FillRect(x + w - lw, y, lw, h - lw, color, blend)

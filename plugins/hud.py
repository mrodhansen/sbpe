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

ELEMS = 'hp hpmax ammo ammomax currency'.split()


class Plugin(PluginBase):
    def onInit(self):
        self.config.options('int', {
            'x': 100,
            'y': 20,
            'size_hp': 42,
            'size_hpmax': 24,
            'size_ammo': 20,
            'size_ammomax': 20,
            'size_currency': 16,
            'outline': 3,
            'spacing': 0,
            'bar_width': 90,
            'bar_height': 4,
            'bar_y': 10
        })
        self.config.options('color', {
            'color_hp': 0xffffffff,
            'color_hp_full': 0xff00ff00,
            'color_ammo': 0xffffff00,
            'color_currency': 0xff9999ff,
            'bar_background': 0xff000000,
            'bar_outline': 0xff808080,
            'bar_color': 0xff0afde1,
            'bar_notches': 0xffffffff,
        })

        for name in ELEMS:
            setattr(self, 'txt_' + name, util.PlainText(font='TenbyFive'))

        self.draw = False

    def afterUpdate(self):
        self.draw = False
        wc = self.refs.WorldClient
        cw = self.refs.ClientWorld
        player = util.find_player(cw) if cw != ffi.NULL else ffi.NULL
        if player == ffi.NULL:
            player = self.refs.player
        if not util.player_looks_alive(player):
            player = ffi.NULL
        if wc == ffi.NULL or cw == ffi.NULL or player == ffi.NULL:
            now = time.perf_counter()
            if now - getattr(self, '_lastlog', 0) > 5:
                self._lastlog = now
                logging.info(
                    'hud skip wc=%s cw=%s player=%s',
                    wc != ffi.NULL, cw != ffi.NULL, player != ffi.NULL)
            return

        hud = self.refs.HUD
        if hud == ffi.NULL:
            hud = wc.hud
        if hud != ffi.NULL and hud.hudStatus != ffi.NULL:
            ffi.cast('struct UIElement *', hud.hudStatus).show = False

        wobj = ffi.cast('struct WorldObject *', player)
        pc = ffi.cast('struct PlayerCharacter *', player)

        self.txt_hp.text = '{}'.format(wobj.props.hitpoints)
        self.txt_hpmax.text = '/{}'.format(wobj.props.maxhitpoints)

        try:
            ammo = int(pc.ammo)
            maxammo = int(pc.maxAmmo.base) + int(pc.maxAmmo.bonus)
            ammomult = int(pc.ammoMult)
            ec = int(pc.ec)
            uc = int(pc.uc)
        except Exception:
            logging.exception('hud player stats')
            return
        if ammo < 0 or ammo > 99999 or maxammo < 0 or maxammo > 99999:
            return

        self.txt_ammo.text = '{}'.format(ammo)
        if ammomult > 1:
            self.txt_ammomax.text = '/{} (x{})'.format(maxammo, ammomult)
        else:
            self.txt_ammomax.text = '/{}'.format(maxammo)

        self.txt_currency.text = '{} EC  {} UC'.format(ec, uc)

        for name in ELEMS:
            el = getattr(self, 'txt_' + name)
            el.size = getattr(self.config, 'size_' + name)
            el.outlineSize = self.config.outline

        if wobj.props.hitpoints == wobj.props.maxhitpoints:
            hpcolor = self.config.color_hp_full
        else:
            hpcolor = self.config.color_hp
        self.txt_hp.color = self.txt_hpmax.color = hpcolor
        self.txt_ammo.color = self.txt_ammomax.color = self.config.color_ammo
        self.txt_currency.color = self.config.color_currency

        self.draw = True
        now = time.perf_counter()
        if now - getattr(self, '_lastlog', 0) > 5:
            self._lastlog = now
            logging.info(
                'hud draw hp=%s/%s ammo=%s xmp=%s ymp=%s tex=%s',
                wobj.props.hitpoints, wobj.props.maxhitpoints,
                ammo, wobj.props.xmp, wobj.props.ymp,
                self.txt_hp._texture)

    def onPresent(self):
        if not self.draw:
            return

        x = self.config.x
        y = self.config.y

        # hp
        self.txt_hp.draw(x, y, anchorX=1, anchorY=0.5)
        self.txt_hpmax.draw(x - 4, y, anchorY=0.5)

        # ammo
        y = y + self.txt_hp.h + self.config.spacing
        self.txt_ammo.draw(x, y, anchorX=1, anchorY=0.5)
        self.txt_ammomax.draw(x - 4, y, anchorY=0.5)

        # currency
        y = y + self.txt_ammo.h + self.config.spacing
        self.txt_currency.draw(x, y, anchorX=0.5)

        # hp bar
        wv = self.refs.WorldView
        cw = self.refs.ClientWorld
        player = util.find_player(cw) if cw != ffi.NULL else self.refs.player
        if wv == ffi.NULL or not util.player_looks_alive(player):
            return
        player = ffi.cast('struct WorldObject *', player)
        props = player.props
        hp = int(props.hitpoints)
        maxhp = int(props.maxhitpoints)
        if maxhp <= 0 or hp < 0:
            return

        width = self.config.bar_width
        if width < 0:
            bw = maxhp
        else:
            bw = width
        bh = self.config.bar_height

        if bw <= 0 or bh <= 0 or bw > 2000:
            return

        ox, oy = _view_offset(wv)
        x = (props.xmp // 256 + props.wmp // 512 - ox) / self.refs.scaleX
        y = (props.ymp // 256 - oy) / self.refs.scaleY
        bx = int(x - bw // 2)
        by = int(y - bh - self.config.bar_y)
        _cw = self.refs.canvasW_[0]
        _ch = self.refs.canvasH_[0]
        self.refs.canvasW_[0] = self.refs.windowW
        self.refs.canvasH_[0] = self.refs.windowH
        now = time.perf_counter()
        if now - getattr(self, '_barlog', 0) > 5:
            self._barlog = now
            logging.info(
                'hud bar bx=%s by=%s bw=%s bh=%s ox=%s oy=%s xmp=%s ymp=%s',
                bx, by, bw, bh, ox, oy, props.xmp, props.ymp)

        # outline
        self.refs.XDL_FillRect(
            bx - 1, by - 1, bw + 2, bh + 2,
            self.config.bar_outline, lib.BLENDMODE_BLEND)
        # background
        self.refs.XDL_FillRect(
            bx, by, bw, bh, self.config.bar_background, lib.BLENDMODE_BLEND)
        # bar
        filled = int(math.ceil(hp * bw / maxhp))
        if filled < 0:
            filled = 0
        if filled > bw:
            filled = bw
        self.refs.XDL_FillRect(
            bx, by, filled, bh, self.config.bar_color, lib.BLENDMODE_BLEND)
        # notches
        st = 1
        while st * 25 < maxhp:
            cx = bx + round(bw * (maxhp - st * 25) / maxhp)
            if cx <= bx + filled:
                break
            self.refs.XDL_FillRect(
                cx, by, 1, bh, self.config.bar_notches, lib.BLENDMODE_BLEND)
            st += 1
        self.refs.canvasW_[0] = _cw
        self.refs.canvasH_[0] = _ch

    def __del__(self):
        wc = self.refs.WorldClient
        if wc != ffi.NULL and wc.hud != ffi.NULL:
            if wc.hud.hudStatus != ffi.NULL:
                ffi.cast('struct UIElement *', wc.hud.hudStatus).show = True

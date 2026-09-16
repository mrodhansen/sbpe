import logging
import math
import os
import platform

from _remote import ffi, lib

refs = None
STRUCTTYPES = ffi.list_types()[1]

GLFUNCTIONS = '''
glGetError glEnable glDisable glHint
glTexImage2D glTexParameteri
'''.split()

GL_TEXTURE_2D = 0x0DE1

GL_TEXTURE_BASE_LEVEL = 0x813C
GL_TEXTURE_MAX_LEVEL = 0x813D

GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_MIN_FILTER = 0x2801

GL_NEAREST_MIPMAP_NEAREST = 0x2700
GL_NEAREST_MIPMAP_LINEAR = 0x2702
GL_LINEAR_MIPMAP_NEAREST = 0x2701
GL_LINEAR_MIPMAP_LINEAR = 0x2703

GL_UNSIGNED_BYTE = 0x1401
GL_RGBA = 0x1908


# classes ####################################################################

class PlainText(object):
    '''wrapper for text drawing'''
    def __init__(self, *, size=14, outlineSize=2, font=b'TenbyFive',
                 color=0xffffff, outlineColor=0x000000, alpha=1.0,
                 screenCoords=True):
        self.size = size
        self.outlineSize = outlineSize
        self.font = font
        self.color = color
        self.outlineColor = outlineColor
        self._texture = 0
        self.text = b''
        self._w = ffi.new('int *')
        self._h = ffi.new('int *')
        self.w = self.h = 0
        self.alpha = alpha
        self._dirty = True
        self.screenCoords = screenCoords

    def __setattr__(self, name, val):
        if name == 'size' or name == 'outlineSize':
            val = int(val)
        elif name == 'color' or name == 'outlineColor':
            val = (int(val) & 0xffffff) | 0xff000000
        elif name == 'font' or name == 'text':
            if type(val) != bytes:
                if type(val) == str:
                    val = val.encode('utf-8')
                else:
                    raise AttributeError(
                        '"{}" must be str or bytes, got {}'.format(
                            name, str(type(val))))
        else:  # anything else is not affecting texture - pass through
            if name == 'alpha':
                val = max(0, min(1, val))
                self._cmod = (math.floor(val * 255) << 24) & 0xff000000
            object.__setattr__(self, name, val)
            return

        # check if the texture needs updating
        try:
            oldval = self.__getattribute__(name)
            if oldval != val:
                self._dirty = True
        except AttributeError:
            pass
        object.__setattr__(self, name, val)

    def updateTexture(self):
        self._texture = refs.XDL_CreateTextTexture(
            self._texture, self.font, self.size, self.color,
            self.text, self.outlineSize, self.outlineColor)
        refs.XDL_QueryTexture(self._texture, self._w, self._h)
        self.w = self._w[0]
        self.h = self._h[0]

    def __del__(self):
        if self._texture > 0:
            refs.XDL_DestroyTexture(self._texture)

    def draw(self, x, y, anchorX=0, anchorY=0, angle=0):
        if self.size <= 0 or len(self.text) == 0:
            return
        if self._dirty:
            self.updateTexture()
            # Retry next frame if the GL text atlas is not ready yet.
            self._dirty = self._texture <= 0
        if self._texture <= 0:
            return

        w = self.w
        h = self.h

        if not self.screenCoords:
            # Canvas/world pixels → window pixels (zoom-safe).
            x /= refs.scaleX
            y /= refs.scaleY

        x -= anchorX * w
        y -= anchorY * h
        rotDegt = round(angle * 10)

        cw = refs.canvasW_[0]
        ch = refs.canvasH_[0]
        refs.canvasW_[0] = refs.windowW
        refs.canvasH_[0] = refs.windowH

        refs.XDL_DrawTexture(
            self._texture, 0, 0, w, h,
            round(x), round(y), w, h,
            rotDegt, w // 2, h // 2,
            0, self._cmod, lib.BLENDMODE_BLEND)

        refs.canvasW_[0] = cw
        refs.canvasH_[0] = ch


class MultilineText(object):
    def __init__(self, *, spacing=15, **kwargs):
        self._kwargs = kwargs
        self.spacing = spacing
        self.children = []

    def __setattr__(self, name, val):
        if name == '_kwargs' or name == 'children':
            pass
        elif name == 'spacing':
            val = int(val)
            if val < 0:
                val = 0
        elif name == 'text':
            lines = val.splitlines()
            if len(self.children) < len(lines):
                for i in range(len(lines) - len(self.children)):
                    self.children.append(PlainText(**self._kwargs))
            for i in range(len(lines)):
                self.children[i].text = lines[i]
            self.children = self.children[:len(lines)]
        else:
            self._kwargs[name] = val
            for t in self.children:
                t.__setattr__(name, val)

        object.__setattr__(self, name, val)

    def draw(self, x, y, **kwargs):
        for t in self.children:
            t.draw(x, y, **kwargs)
            y += self.spacing
            if y > refs.windowH:
                return


class NumberDict(PlainText):
    def __init__(self, *, size=16, outlineSize=2, font=b'HemiHeadBold',
                 color=0xffffff, outlineColor=0x000000, screenCoords=False):
        super().__init__(
            size=size, outlineSize=outlineSize, font=font,
            color=color, outlineColor=outlineColor, screenCoords=screenCoords)

    def updateTexture(self):
        self._texture = refs.XDL_GetNumberDict(
            self.font, self.size, self.color,
            self.outlineSize, self.outlineColor)
        refs.XDL_QueryTexture(self._texture, self._w, self._h)
        self.w = self._w[0]
        self.h = self._h[0]

    def draw(self, num, x, y, anchorX=0, anchorY=0):
        if self.size <= 0:
            return
        if self._dirty:
            self.updateTexture()
            self._dirty = False
        if self._texture <= 0:
            return

        _nw = ffi.new('int *')
        _nh = ffi.new('int *')
        try:
            num = int(num)
        except (TypeError, ValueError):
            return
        if num < -99999 or num > 99999:
            return
        refs.XDL_SizeFromNumberDict(self._texture, num, _nw, _nh)
        nw = _nw[0]
        nh = _nh[0]

        if not self.screenCoords:
            x /= refs.scaleX
            y /= refs.scaleY

        x -= anchorX * nw
        y -= anchorY * nh

        cw = refs.canvasW_[0]
        ch = refs.canvasH_[0]
        refs.canvasW_[0] = refs.windowW
        refs.canvasH_[0] = refs.windowH

        refs.XDL_DrawFromNumberDict(
            self._texture, num, nw, nh, round(x), round(y), nw, nh)

        refs.canvasW_[0] = cw
        refs.canvasH_[0] = ch

    def __del__(self):
        pass


# functions ##################################################################

def loadGLFunctions():
    for name in GLFUNCTIONS:
        refs[name] = ffi.cast(
            'p' + name, lib.SDL_GL_GetProcAddress(bytes(name, 'utf-8')))


def loadMipmaps(pname, sheet):
    from PIL import Image

    if GLFUNCTIONS[0] not in refs:
        loadGLFunctions()

    maxlevel = refs.config.getint('general', 'mipmap_maxlevel', fallback=0)
    if maxlevel < 1:
        return

    for level in range(1, maxlevel + 1):
        rel = 'data/texture/{}.m{}.{}.png'.format(pname, level, sheet)
        candidates = [rel, os.path.join(os.getcwd(), rel)]
        img = None
        for fname in candidates:
            try:
                img = Image.open(fname)
                break
            except IOError:
                continue
        if img is None:
            logging.error('could not load: "{}"'.format(rel))
            return
        pixels = ffi.from_buffer(img.tobytes())
        refs.glTexImage2D(
            GL_TEXTURE_2D, level, 4, img.width, img.height,
            0, GL_RGBA, GL_UNSIGNED_BYTE, pixels)

        logging.debug('pack {} sheet {} level {} err {}'.format(
            pname, sheet, level, refs.glGetError()))

    refs.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, maxlevel)
    refs.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                         GL_LINEAR_MIPMAP_LINEAR)


def updateState():
    '''make commonly used data more accessible to plugins'''
    if GLFUNCTIONS[0] not in refs:
        loadGLFunctions()

    # window size and scale from SDL — safe on every platform
    if refs.window_[0] != ffi.NULL:
        ww_ = ffi.new('int *')
        wh_ = ffi.new('int *')
        lib.SDL_GetWindowSize(refs.window_[0], ww_, wh_)
        ww = ww_[0]
        wh = wh_[0]
        if ww > 0 and wh > 0:
            refs.windowW = ww
            refs.windowH = wh
        elif refs.canvasW_[0] > 0 and refs.canvasH_[0] > 0:
            refs.windowW = refs.canvasW_[0]
            refs.windowH = refs.canvasH_[0]
        if refs.windowW > 0 and refs.windowH > 0:
            refs.scaleX = refs.canvasW_[0] / refs.windowW
            refs.scaleY = refs.canvasH_[0] / refs.windowH
        if not getattr(updateState, '_loggedWin', False) and refs.windowW:
            logging.info(
                'window %dx%d canvas %dx%d scale %.3f,%.3f',
                refs.windowW, refs.windowH,
                refs.canvasW_[0], refs.canvasH_[0],
                refs.scaleX, refs.scaleY)
            updateState._loggedWin = True

    if refs.stage[0] == ffi.NULL:
        return

    # top level children of stage
    tops = []
    types = []
    for t in vec2list(refs.stage[0].asUIElementContainer.children):
        clname = getClassName(t)
        types.append(clname)
        if clname in STRUCTTYPES:
            t = ffi.cast('struct {} *'.format(clname), t)
        tops.append(t)
    refs.tops = tops
    refs.topTypes = types
    if types and types != getattr(updateState, '_lastTypes', None):
        logging.info('stage tops: %s', types)
        updateState._lastTypes = list(types)

    if not types:
        refs.MainMenu = refs.GameClient = refs.WorldClient = refs.ClientWorld =\
            refs.WorldView = refs.HUD = refs.player = refs.serverSubWorld = ffi.NULL
        return

    # main menu
    if types[0] == 'MainMenu':
        refs.MainMenu = tops[0]
    else:
        refs.MainMenu = ffi.NULL

    # GameClient.worldClient is 0x80 / WorldClient.clientWorld 0x78 on Mac.
    # Probe the page before vtable reads so a stale pointer cannot SIGSEGV.
    refs.GameClient = refs.WorldClient = refs.ClientWorld =\
        refs.WorldView = refs.HUD = refs.player = refs.serverSubWorld = ffi.NULL

    if types[0] == 'GameClient':
        refs.GameClient = tops[0]
        # UI-find WorldClient only. Do not getClassName nested pointers —
        # that SIGSEGVd in hook_Clear once WC appeared.
        wc = _ui_find(refs.GameClient, 'WorldClient')
        if wc != ffi.NULL:
            refs.WorldClient = wc
            cw = wc.clientWorld
            wv = wc.worldView
            hud = wc.hud
            # ClientWorld is malloc(0x520); require the whole object, not just
            # the first pointer, before reading player / serverSubWorld.
            if _ptr_mapped(cw, 0x520):
                refs.ClientWorld = ffi.cast('struct ClientWorld *', cw)
                plr = player_ptr(refs.ClientWorld)
                refs.player = plr if player_looks_alive(plr) else ffi.NULL
                ssw = refs.ClientWorld.serverSubWorld
                refs.serverSubWorld = ssw if _ptr_mapped(ssw, 8) else ffi.NULL
                if platform.system() == 'Darwin':
                    mac_ssw = _mac_load_ptr(
                        cw, _MAC_CW_SERVER, 'struct ForeignSubWorld *')
                    if mac_ssw != ffi.NULL:
                        refs.serverSubWorld = mac_ssw
            if _ptr_mapped(wv, 64):
                refs.WorldView = ffi.cast('struct WorldView *', wv)
            if _ptr_mapped(hud, 64):
                refs.HUD = ffi.cast('struct HUD *', hud)
        _log_world_bind()

    # add more useful things here


def _stdstring_raw(stdstring):
    if stdstring == ffi.NULL:
        return ffi.NULL
    if ffi.typeof(stdstring).kind == 'pointer':
        return stdstring
    return ffi.addressof(stdstring)


def getstr(stdstring):
    if stdstring == ffi.NULL:
        return '(NULL)'
    if platform.system() == 'Darwin':
        # libc++ std::string (Apple / darwin13 layout): 24-byte SSO.
        # long: byte0 LSB=1, size at +8, data ptr at +16
        # short: byte0 = size<<1, chars at +1
        raw = _stdstring_raw(stdstring)
        if not _ptr_ok(raw):
            return '(NULL)'
        base = ffi.cast('unsigned char *', raw)
        first = int(base[0])
        if first & 1:
            n = int(ffi.cast('uint64_t *', raw)[1])
            ptr = ffi.cast('char **', raw)[2]
            if not _ptr_ok(ptr) or n > 10000:
                return '(NULL)'
        else:
            n = first >> 1
            if n > 22:
                return '(NULL)'
            ptr = ffi.cast('char *', raw) + 1
        if ptr == ffi.NULL or n > 10000:
            return '(NULL)'
        if n <= 0:
            return ''
        return bytes(ffi.buffer(ptr, n)).decode('utf-8', errors='replace')
    if not hasattr(stdstring, 's') or stdstring.s == ffi.NULL:
        return '(NULL)'
    if not _ptr_ok(stdstring.s):
        return '(NULL)'
    return ffi.string(stdstring.s, 1000).decode('utf-8', errors='replace')


def clear_stdstring(stdstring):
    '''Zero a std::string length in place (hide_shells / hide_factions).'''
    if stdstring == ffi.NULL:
        return
    if platform.system() == 'Darwin':
        raw = _stdstring_raw(stdstring)
        if not _ptr_ok(raw):
            return
        base = ffi.cast('unsigned char *', raw)
        if int(base[0]) & 1:
            ffi.cast('uint64_t *', raw)[1] = 0
        else:
            base[0] = 0
        return
    if not hasattr(stdstring, 's') or stdstring.s == ffi.NULL:
        return
    if not _ptr_ok(stdstring.s):
        return
    ffi.cast('int *', stdstring.s)[-3] = 0


def client_time_reserve(cw):
    '''ClientWorld net-lag buffer. Mac stores the 1000-clamped value at inputBuffer.'''
    if cw == ffi.NULL:
        return 0
    if platform.system() == 'Darwin':
        return cw.inputBuffer
    return cw.timeReserve


# Mac Steam mvmmoclient field offsets (from WorldView::updateOffsets /
# ClientWorld::getObjs / handleCreatePlayer). generated.h is short by 16
# bytes in WorldView, so wv.offset is not at 0xbc there.
_MAC_WV_OFFSET = 0xbc
_MAC_CW_SERVER = 0x498
_MAC_CW_MYSUB = 0x4e0
_MAC_CW_PLAYER = 0x4f0


def view_offset(wv):
    '''Camera top-left in world pixels.'''
    if wv == ffi.NULL:
        return 0, 0
    if platform.system() == 'Darwin':
        xy = ffi.cast('int32_t *', int(ffi.cast('uintptr_t', wv)) + _MAC_WV_OFFSET)
        return int(xy[0]), int(xy[1])
    return int(wv.offset.x), int(wv.offset.y)


def is_player_class(cname):
    if cname in refs.CASTABLE.get('PlayerCharacter', ()):
        return True
    if cname in refs.CASTABLE.get('Player', ()):
        return True
    return False


def player_ptr(cw):
    '''ClientWorld.player, using the Mac field offset when layouts diverge.'''
    if cw == ffi.NULL:
        return ffi.NULL
    if platform.system() == 'Darwin':
        plr = _mac_load_ptr(cw, _MAC_CW_PLAYER, 'struct PlayerCharacter *')
        if plr != ffi.NULL:
            return plr
    plr = cw.player
    if plr == ffi.NULL or not _ptr_mapped(plr, 0x100):
        return ffi.NULL
    return ffi.cast('struct PlayerCharacter *', plr)


def player_looks_alive(plr):
    '''Reject stale/garbage player pointers (zone-join UAF).'''
    if plr == ffi.NULL or not _ptr_mapped(plr, 0x100):
        return False
    try:
        p = ffi.cast('struct WorldObject *', plr).props
        if p.wmp <= 0 or p.hmp <= 0:
            return False
        if p.wmp > 1024 * 256 or p.hmp > 1024 * 256:
            return False
        if p.maxhitpoints <= 0 or p.maxhitpoints > 10000:
            return False
        if p.hitpoints < 0 or p.hitpoints > p.maxhitpoints + 100:
            return False
        return True
    except Exception:
        return False


def collect_objects(cw):
    if cw == ffi.NULL:
        return []
    sw = cw.serverSubWorld
    my = cw.mySubWorld
    if platform.system() == 'Darwin':
        mac_sw = _mac_load_ptr(cw, _MAC_CW_SERVER, 'struct ForeignSubWorld *')
        if mac_sw != ffi.NULL:
            sw = mac_sw
        mac_my = _mac_load_ptr(cw, _MAC_CW_MYSUB, 'struct ClientSubWorld *')
        if mac_my != ffi.NULL:
            my = mac_my
    out = worldobjects(sw)
    if my != ffi.NULL:
        out += worldobjects(ffi.addressof(my.asNativeSubWorld))
    out += vec2list(cw.allies, 'struct WorldObject *')
    return out


def find_player(cw, objects=None):
    if cw == ffi.NULL:
        return ffi.NULL
    plr = player_ptr(cw)
    if player_looks_alive(plr):
        return plr
    return ffi.NULL


def _mac_load_ptr(obj, off, typename):
    if obj == ffi.NULL:
        return ffi.NULL
    p = ffi.cast('void **', int(ffi.cast('uintptr_t', obj)) + off)[0]
    if not _ptr_mapped(p, 8):
        return ffi.NULL
    return ffi.cast(typename, p)


def veclen(vector, itemtype='void*'):
    if not _ptr_ok(vector.start) or not _ptr_ok(vector.finish):
        return 0
    if vector.finish < vector.start:
        return 0
    return (vector.finish - vector.start) // ffi.sizeof(itemtype)


def vec2list(vector, itemtype='void*'):
    '''std::vector -> list'''
    if not _ptr_ok(vector.start) or not _ptr_ok(vector.finish):
        return []
    if vector.endOfStorage < vector.finish or vector.finish < vector.start:
        return []
    n = (vector.finish - vector.start) // ffi.sizeof(itemtype)
    if n <= 0:
        return []
    if n > 256:
        n = 256
    nbytes = n * ffi.sizeof(itemtype)
    if nbytes <= 0 or not _ptr_mapped(vector.start, nbytes):
        return []
    return ffi.unpack(ffi.cast(itemtype + '*', vector.start), n)


def sVecMap2list(svecmap, itemtype='void*'):
    '''struct SortedVecMap -> list'''
    lst = vec2list(svecmap.vec, 'struct SortedVecElement')
    out = []
    for el in lst:
        if not _ptr_ok(el.obj):
            continue
        out.append(ffi.cast(itemtype, el.obj))
    return out


def worldobjects(subworld):
    '''get objects from a member of subclass of SubWorldImpl as a list'''
    if subworld == ffi.NULL:
        return []
    if ffi.typeof(subworld).kind != 'pointer':
        subworld = ffi.addressof(subworld)
    if not _ptr_ok(subworld):
        return []
    return sVecMap2list(subworld.asSubWorldImpl.objs, 'struct WorldObject *')


def _ptr_ok(ptr):
    if ptr == ffi.NULL:
        return False
    if ffi.typeof(ptr).kind != 'pointer':
        return True
    addr = int(ffi.cast('uintptr_t', ptr))
    sysname = platform.system()
    if sysname == 'Darwin':
        return addr % 8 == 0 and 0x100000000 <= addr <= 0x7fffffffffff
    if sysname == 'Linux':
        return addr % 8 == 0 and 0x400000 <= addr <= 0x7fffffffffff
    return addr >= 0x10000


_libc = None
_PAGESIZE = 4096


def _ptr_mapped(ptr, nbytes=8):
    '''True if [ptr, ptr+nbytes) is in a mapped page. Avoids SIGSEGV on Mac.'''
    if not _ptr_ok(ptr):
        return False
    sysname = platform.system()
    if sysname not in ('Darwin', 'Linux'):
        return True
    global _libc
    if _libc is None:
        import ctypes
        import ctypes.util
        name = ctypes.util.find_library('c')
        _libc = ctypes.CDLL(name, use_errno=True)
        _libc.mincore.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        _libc.mincore.restype = ctypes.c_int
    import ctypes
    addr = int(ffi.cast('uintptr_t', ptr))
    end = addr + int(nbytes)
    vec = ctypes.create_string_buffer(1)
    page = addr & ~(_PAGESIZE - 1)
    while page < end:
        if _libc.mincore(page, _PAGESIZE, vec) != 0:
            return False
        page += _PAGESIZE
    return True


def _game_image_range():
    rng = getattr(_game_image_range, '_rng', None)
    if rng is not None:
        return rng
    slide = int(lib.sbpe_image_slide())
    base = 0x100000000 + slide
    rng = (base, base + 0x800000)
    _game_image_range._rng = rng
    return rng


def _in_game_image(ptr):
    if not _ptr_ok(ptr):
        return False
    if platform.system() != 'Darwin':
        return True
    addr = int(ffi.cast('uintptr_t', ptr))
    lo, hi = _game_image_range()
    return lo <= addr < hi


def _is_container(cname):
    return cname in refs.CASTABLE.get('UIElementContainer', ())


def _ui_find(obj, classname, depth=4):
    '''Find a named UI object under obj. Used when generated.h offsets miss.'''
    if obj == ffi.NULL or depth < 0:
        return ffi.NULL
    if getClassName(obj) == classname:
        if classname in STRUCTTYPES:
            return ffi.cast('struct {} *'.format(classname), obj)
        return obj
    if not _is_container(getClassName(obj)):
        return ffi.NULL
    uiec = ffi.cast('struct UIElementContainer *', obj)
    for elem in vec2list(uiec.children):
        found = _ui_find(elem, classname, depth - 1)
        if found != ffi.NULL:
            return found
    return ffi.NULL


def _log_world_bind():
    key = (
        refs.WorldClient != ffi.NULL,
        refs.ClientWorld != ffi.NULL,
        refs.WorldView != ffi.NULL,
    )
    if key == getattr(_log_world_bind, '_last', None):
        return
    _log_world_bind._last = key
    logging.info(
        'world bind WC=%s CW=%s WV=%s',
        'WorldClient' if refs.WorldClient != ffi.NULL else 'NULL',
        'ClientWorld' if refs.ClientWorld != ffi.NULL else 'NULL',
        'WorldView' if refs.WorldView != ffi.NULL else 'NULL',
    )


def getClassName(obj):
    '''
    class name of a C++ object (assuming gcc memory layout).
    doesn't demangle complicated names
    '''
    if obj == ffi.NULL:
        return 'NULL'
    ptrsz = ffi.sizeof('void *')
    if not _ptr_mapped(obj, ptrsz):
        return 'UNKNOWN'

    # class pointer is always at [0]
    classptr = ffi.cast('void****', obj)[0]
    if not _in_game_image(classptr):
        return 'UNKNOWN'
    typeinfo_slot = ffi.cast('void *', int(ffi.cast('uintptr_t', classptr)) - ptrsz)
    if not _ptr_mapped(typeinfo_slot, ptrsz):
        return 'UNKNOWN'
    typeinfo = classptr[-1]
    if not _in_game_image(typeinfo):
        return 'UNKNOWN'
    if not _ptr_mapped(typeinfo, 2 * ptrsz):
        return 'UNKNOWN'
    nameptr = typeinfo[1]
    if not _ptr_mapped(nameptr, 64):
        return 'UNKNOWN'
    raw = bytes(ffi.buffer(nameptr, 64))
    n = raw.find(b'\x00')
    if n < 2:
        return 'UNKNOWN'
    cname = raw[:n]
    return cname[1 if len(cname) < 11 else 2:].decode('ascii', errors='replace')


def firstChild(obj):
    if not _ptr_ok(obj):
        return ffi.NULL
    cv = ffi.cast('struct UIElementContainer *', obj).children
    kids = vec2list(cv, 'struct UIElement *')
    if not kids:
        return ffi.NULL
    return kids[0]


def getUITree(obj, depth=0):
    '''printable UI element tree starting from obj'''
    if obj == ffi.NULL:
        return ' ' * depth + 'NULL'
    cname = getClassName(obj)
    uiel = ffi.cast('struct UIElement*', obj)
    res = ' ' * depth + '{0} ({1.x}, {1.y}) {1.w}x{1.h}'.format(cname, uiel)
    if cname in refs.CASTABLE['UIElementContainer']:
        uiec = ffi.cast('struct UIElementContainer*', obj)
        for elem in vec2list(uiec.children):
            res += '\n' + getUITree(elem, depth + 2)
    return res

import configparser
import importlib
import json
import logging
import os
import platform
import sys
import time

from _remote import ffi, lib

util = None

VERSION = 'v1.7.0'
LOGFILE = 'remote.log'
CONFIGFILE = 'config.ini'

# non-function imported symbol types
SYMTYPES = {
    'stage': 'struct Stage **',
    'window_': 'void **',
    'canvasW_': 'int *',
    'canvasH_': 'int *',
    'userData_': 'void **',
    'WorldClient::handleWindowEvent': 'void *',
    'UIElementContainer::handleWindowEvent': 'void *'
}


class dotdict(dict):
    '''dict with dot notation accessors'''
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


# various references to pass around (symbols, constants, etc)
refs = dotdict(
    tops=[],  # collection of direct children of stage
    topTypes=[],  # the class names of tops
    MainMenu=ffi.NULL,
    GameClient=ffi.NULL,
    WorldClient=ffi.NULL,
    ClientWorld=ffi.NULL,
    WorldView=ffi.NULL,
    overrideW=0, overrideH=0,  # fake values to return from XDL_GetWindowSize
    windowW=0, windowH=0,  # current window size
    scaleX=1, scaleY=1,    # current window scale
    lastMove=0,  # timestamp of last mouse movement
    _tfname='',
    _tex_user_cb=None,
)

refs.VERSION = VERSION
refs.SYSINFO = '{0} v{1} {2} @ {3}'.format(
    platform.python_implementation(), platform.python_version(),
    platform.architecture()[0], platform.platform())

refs.config = configparser.ConfigParser()
refs.config.optionxform = str

refs.manager = None


# hooks ######################################################################

ORIGS = {}


@ffi.def_extern()
def hook_DoEvents():
    refs.manager.run()
    refs.manager.runCallbacks('beforeUpdate')
    ORIGS['XDL_DoEvents']()


@ffi.def_extern()
def hook_Clear(color):
    try:
        util.updateState()
        refs.manager.runCallbacks('afterUpdate')
    except Exception:
        logging.exception('hook_Clear')
    if refs.stage[0] != ffi.NULL:
        ORIGS['XDL_Clear'](refs.stage[0].backgroundColor)
    else:
        ORIGS['XDL_Clear'](color)


@ffi.def_extern()
def hook_Present():
    try:
        refs.manager.runCallbacks('onPresent')
    except Exception:
        logging.exception('hook_Present')
    ORIGS['XDL_Present']()


@ffi.def_extern()
def hook_GetWindowSize(wptr, hptr):
    if refs.overrideW > 0:
        wptr[0] = refs.overrideW
        hptr[0] = refs.overrideH
    else:
        lib.SDL_GetWindowSize(refs.window_[0], wptr, hptr)


@ffi.def_extern()
def hook_TrackGAEvent(category, action, label, value):
    pass
    # logging.debug('GA: {}.{} "{}" {}'.format(
    #     ffi.string(category).decode(), ffi.string(action).decode(),
    #     ffi.string(label).decode(), value))


@ffi.def_extern()
def hook_mouseMove(x, y, userData):
    x = round(x * refs.scaleX)
    y = round(y * refs.scaleY)
    ORIGS['mouseMoveCallback'](x, y, userData)
    refs.lastMove = time.perf_counter()


@ffi.def_extern()
def hook_mouseButton(x, y, button, down, userData):
    x = round(x * refs.scaleX)
    y = round(y * refs.scaleY)
    ORIGS['mouseButtonCallback'](x, y, button, down, userData)


@ffi.def_extern()
def hook_LoadTextureFile(name, callback, userData):
    try:
        refs._tfname = ffi.string(name).decode()
        logging.info('loadtex %s', refs._tfname)
        refs._tex_user_cb = callback
        ORIGS['XDL_LoadTextureFile'](name, lib.hook_textureCallback, userData)
    except Exception:
        logging.exception('hook_LoadTextureFile')
        ORIGS['XDL_LoadTextureFile'](name, callback, userData)


@ffi.def_extern()
def hook_textureCallback(texture, sptr):
    try:
        cb = refs._tex_user_cb
        if cb is not None:
            cb(texture, sptr)
    except Exception:
        logging.exception('orig texture callback')
    try:
        path = refs._tfname.replace('\\', '/').split('/')
        fname = path[-1]
        folder = path[-2] if len(path) >= 2 else ''
        parts = fname.split('.')
        if folder != 'texture' or len(parts) < 3 or parts[1] != 'm0':
            return
        util.loadMipmaps(parts[0], parts[2])
    except Exception:
        logging.exception('loadMipmaps')


def initHooks():
    ok = True

    def addhook(fname, hookfunc, ret=False):
        nonlocal ok
        hook = lib.subhook_new(refs[fname], hookfunc, 1)
        orig = ffi.cast('p' + fname, lib.subhook_get_trampoline(hook))
        # LoadTextureFile trampoline crashes under Rosetta; always wrap.
        if orig != ffi.NULL and fname != 'XDL_LoadTextureFile':
            ORIGS[fname] = orig
        else:
            logging.info('{}: no trampoline, using fallback'.format(fname))

            def call_orig(*args):
                lib.subhook_remove(hook)
                res = refs[fname](*args)
                lib.subhook_install(hook)
                if ret:
                    return res

            ORIGS[fname] = call_orig

        lib.subhook_install(hook)
        if not lib.subhook_is_installed(hook):
            logging.error('failed to hook {}'.format(fname))
            ok = False

    addhook('XDL_DoEvents', lib.hook_DoEvents)
    addhook('XDL_Clear', lib.hook_Clear)
    addhook('XDL_Present', lib.hook_Present)
    addhook('XDL_GetWindowSize', lib.hook_GetWindowSize)
    addhook('XDL_TrackGAEvent', lib.hook_TrackGAEvent)
    addhook('mouseMoveCallback', lib.hook_mouseMove)
    addhook('mouseButtonCallback', lib.hook_mouseButton)

    if refs.config.getboolean('general', 'mipmaps', fallback=False):
        if platform.system() == 'Darwin':
            # subhooking LoadTextureFile / its completion cb abort()s under
            # Rosetta. m0 sheets from dataVersion.m.bpb still load.
            logging.info('mipmaps: m0 sheets only (skipping LoadTextureFile hook)')
        else:
            addhook('XDL_LoadTextureFile', lib.hook_LoadTextureFile)
    return ok


# startup ####################################################################

@ffi.def_extern()
def kickstart():
    global refs, util
    try:
        return _kickstart()
    except Exception:
        try:
            logging.exception('kickstart failed')
        except Exception:
            sys.stderr.write('kickstart failed\n')
        return 1


def _kickstart():
    global refs, util

    SYMFILE = os.environ['SBPE_SYMFILE']

    with open(SYMFILE, 'rb') as f:
        offsets = json.loads(f.read().decode('utf-8'))

    refs.SCRIPTPATH = os.path.dirname(SYMFILE)
    sys.path.insert(1, refs.SCRIPTPATH)
    sys.path.insert(1, os.path.join(refs.SCRIPTPATH, 'pypy', 'site-packages'))

    logging.basicConfig(
        level=logging.INFO,
        filename=os.path.join(refs.SCRIPTPATH, LOGFILE), filemode='w',
        format='%(asctime)s %(module)s [%(levelname)s] %(message)s')

    logging.info('SBPE ' + refs.VERSION)
    logging.info('platform: ' + refs.SYSINFO)

    refs.CONFIGFILE = os.path.join(refs.SCRIPTPATH, CONFIGFILE)
    if not refs.config.read(refs.CONFIGFILE):
        logging.error('could not read {}'.format(refs.CONFIGFILE))
        return 1

    slide = 0
    if platform.system() in ('Darwin', 'Linux'):
        slide = lib.sbpe_image_slide()
        if not lib.sbpe_image_found():
            logging.error('could not find game image for ASLR slide')
            return 1
        logging.info('image slide: 0x{:x}'.format(slide))

    # import native functions/objects
    for sname in offsets:
        offset = offsets[sname] + slide
        if sname in SYMTYPES:
            # objects, variables
            refs[sname] = ffi.cast(SYMTYPES[sname], offset)
        elif sname.startswith('flags::'):
            # flags
            refs[sname[7:]] = ffi.cast('uint8_t *', offset)
        else:
            # functions
            refs[sname] = ffi.cast('p' + sname, offset)

    # import object inheritance info
    with open(os.path.join(refs.SCRIPTPATH, 'cdefs/proto.json'), 'r') as f:
        d = json.load(f)
        refs.CHAINS = d['chains']
        refs.CASTABLE = d['castable']

    # import util
    util = importlib.import_module('util')
    util.refs = refs

    # import plugins
    plugpath = os.path.join(refs.SCRIPTPATH, 'plugins')
    man = importlib.import_module('manager')
    refs.manager = man.Manager(path=plugpath, refs=refs)

    if not initHooks():
        logging.error('required hooks failed')
        return 1

    for key in ('SBPE_SYMFILE', 'PYTHONPATH', 'PYTHONHOME'):
        os.environ.pop(key, None)

    logging.info('startup ok')
    return 0

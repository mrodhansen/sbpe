import configparser
import json
import os
import platform
import shutil
import subprocess
import sys
import time

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

if platform.system() == 'Windows':
    from mayhem.proc import ProcessError
    from mayhem.proc.native import NativeProcess

BASE = 0x400000


def default_game_path():
    sysname = platform.system()
    if sysname == 'Windows':
        return r'c:\Program Files (x86)\Steam\SteamApps\common\StarBreak\mvmmoclient.exe'
    if sysname == 'Linux':
        candidates = [
            os.path.expanduser('~/.steam/steam/steamapps/common/StarBreak/mvmmoclient'),
            os.path.expanduser('~/.local/share/Steam/steamapps/common/StarBreak/mvmmoclient'),
            os.path.expanduser('~/.steam/root/steamapps/common/StarBreak/mvmmoclient'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return candidates[0]
    if sysname == 'Darwin':
        steam = os.path.expanduser(
            '~/Library/Application Support/Steam/steamapps/common/StarBreak')
        candidates = [
            os.path.join(steam, 'MVMMOClient.app', 'Contents', 'MacOS', 'mvmmoclient'),
            os.path.join(steam, 'StarBreak.app', 'Contents', 'MacOS', 'mvmmoclient'),
            os.path.join(steam, 'StarBreak.app', 'Contents', 'MacOS', 'StarBreak'),
            os.path.join(steam, 'mvmmoclient'),
            os.path.join(steam, 'StarBreak'),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return candidates[0]
    return ''


DEFAULTGAMEPATH = default_game_path()

SCRIPTPATH = os.path.dirname(__file__) or os.getcwd()
REMOTEPATH = os.path.normpath(os.path.join(SCRIPTPATH, 'build/remote.bin'))
SYMQPATH = 'symquery/{}/bin/symquery'.format(platform.system())
SYMQPATH = os.path.normpath(os.path.join(SCRIPTPATH, SYMQPATH))
CONFFILE = os.path.join(SCRIPTPATH, 'config.ini')
CONF_TEMPLATE = os.path.join(SCRIPTPATH, 'config_template.ini')
SYMFILE = os.path.join(SCRIPTPATH, 'symbols.json')

# offsets of these symbols will be passed to the remote
SYMLIST = '''
stage window_ canvasW_ canvasH_
mouseMoveCallback mouseButtonCallback windowEventCallback
userData_

XDL_GetWindowSize
XDL_DoEvents XDL_Clear XDL_Present
XDL_DrawPoint XDL_DrawLine XDL_DrawRect XDL_DrawTri XDL_FillRect XDL_FillTri
XDL_LoadTextFile XDL_LoadTextureFile XDL_CreateTextTexture
XDL_CalculateTextWidth XDL_LineSpacing
XDL_QueryTexture XDL_DrawTexture
XDL_GetNumberDict XDL_DrawFromNumberDict XDL_SizeFromNumberDict
XDL_DestroyTexture
XDL_TrackGAEvent

flags::drawHUD
flags::drawTiles
flags::drawEdge
flags::drawScatter
flags::drawUniform
flags::drawHitBoxes
flags::drawBoxes

WorldClient::handleWindowEvent
UIElementContainer::handleWindowEvent
'''.split()


def _match_wanted(raw_name, wanted):
    name = raw_name.strip()
    if name.startswith('_') and '::' not in name:
        name = name[1:]
    base = name.split('(', 1)[0]
    if base in wanted:
        return base
    return None


def resolveSymbolsSymquery(exepath):
    wanted = list(SYMLIST)
    offsets = {}
    lines = subprocess.check_output([SYMQPATH, '-e', exepath, '--list'])
    lines = lines.decode().splitlines()
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        sname = parts[0]
        if sname not in wanted:
            continue
        wanted.remove(sname)
        off = parts[1].split('-')[0]
        try:
            off = BASE + int(off, 16)
        except ValueError:
            logging.error('invalid offset "{0}" for "{1}"'.format(off, sname))
            return None
        offsets[sname] = off

    if wanted:
        logging.error('could not find symbols: ' + ' '.join(wanted))
        return None
    return offsets


def resolveSymbolsNm(exepath):
    wanted = set(SYMLIST)
    offsets = {}
    cmd = ['nm', '-C', exepath]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as err:
        detail = getattr(err, 'output', b'') or str(err).encode()
        logging.error('nm failed: %s', detail.decode(errors='replace'))
        return None

    for line in output.decode(errors='replace').splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] in ('(for', 'architecture'):
            continue
        try:
            addr = int(parts[0], 16)
        except ValueError:
            continue
        stype = parts[1]
        if stype in ('U', 'u'):
            continue
        raw_name = ' '.join(parts[2:])
        sname = _match_wanted(raw_name, wanted)
        if sname is None or sname in offsets:
            continue
        offsets[sname] = addr
        wanted.remove(sname)
        if not wanted:
            break

    if wanted:
        logging.error('could not find symbols: ' + ' '.join(sorted(wanted)))
        return None
    return offsets


def resolveSymbols(exepath):
    if platform.system() == 'Windows':
        return resolveSymbolsSymquery(exepath)
    return resolveSymbolsNm(exepath)


def windows_startup_offset():
    startup = subprocess.check_output([
        SYMQPATH, '-e', REMOTEPATH, '-s', 'kickstart'])
    return int(startup.strip(), 16)


def launch(exepath):
    env = os.environ.copy()
    cmd = [exepath]
    cwd = os.path.dirname(exepath)
    if platform.system() == 'Linux':
        env['LD_PRELOAD'] = os.path.abspath(REMOTEPATH)
        py_site = os.path.join(sys.prefix, 'lib',
            'python{}.{}'.format(sys.version_info.major, sys.version_info.minor),
            'site-packages')
        env['PYTHONPATH'] = os.pathsep.join([py_site, SCRIPTPATH])
        env['PYTHONHOME'] = sys.base_prefix
        logging.info('LD_PRELOAD %s', env['LD_PRELOAD'])
    if platform.system() == 'Darwin':
        launcher = os.path.normpath(os.path.join(SCRIPTPATH, 'build/launcher_mac'))
        if not os.path.isfile(launcher):
            logging.error('missing %s — compile launcher_mac.c', launcher)
            return False
        pyhome = '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9'
        if not os.path.isdir(pyhome):
            logging.error('missing PYTHONHOME %s', pyhome)
            return False
        cmd = [launcher, os.path.abspath(REMOTEPATH), os.path.abspath(exepath)]
        env['PYTHONHOME'] = pyhome
        env['PYTHONNOUSERSITE'] = '1'
        x86_site = os.path.join(SCRIPTPATH, '.venv-x86', 'lib', 'python3.9', 'site-packages')
        if not os.path.isdir(x86_site):
            logging.error('missing %s — run ./build.sh', x86_site)
            return False
        env['PYTHONPATH'] = os.pathsep.join([x86_site, SCRIPTPATH])
        logging.info('inserting %s via %s', REMOTEPATH, launcher)

    # start the game
    if platform.system() == 'Windows':
        popen_out, popen_err = subprocess.PIPE, subprocess.STDOUT
    else:
        popen_out, popen_err = subprocess.DEVNULL, subprocess.DEVNULL
    gpop = subprocess.Popen(
        cmd, stderr=popen_err, stdout=popen_out, cwd=cwd, env=env)

    # on Windows, inject into the running process
    if platform.system() == 'Windows':
        try:
            proc = NativeProcess(pid=gpop.pid)
            lib_h = proc.load_library(REMOTEPATH)
            addr = lib_h + windows_startup_offset()
            thr = proc.start_thread(addr)
            logging.info('starting from 0x{:x}'.format(addr))
            proc.join_thread(thr)
        except ProcessError as error:
            gpop.kill()
            logging.error(error.msg)
            return False

    return gpop


def runLoader(exepath=''):
    mipmaps = False

    # ensure config exists
    if not os.path.exists(CONFFILE):
        logging.info('config not found, copying from template')
        try:
            shutil.copy(CONF_TEMPLATE, CONFFILE)
        except Exception:
            logging.error('failed to copy config!')
            return False

    conf = configparser.ConfigParser()
    conf.read(CONFFILE)
    exepath = os.path.expanduser(conf.get('general', 'game', fallback=exepath))
    if not os.path.isfile(exepath):
        discovered = default_game_path()
        if os.path.isfile(discovered):
            logging.info('config game path missing, using %s', discovered)
            exepath = discovered
    mipmaps = conf.getboolean('general', 'mipmaps', fallback=False)
    keepopen = conf.getboolean('general', 'keep_open', fallback=False)
    if os.environ.get('SBPE_WAIT', '').strip() in ('1', 'yes', 'true'):
        keepopen = True

    # ensure the binary exists
    if not os.path.isfile(exepath):
        logging.error('put the path to the game executable in ' + CONFFILE)
        return False

    if not os.path.isfile(REMOTEPATH):
        logging.error('missing %s — run builder.py first', REMOTEPATH)
        return False

    # resolve symbols and prepare environment
    offsets = resolveSymbols(exepath)
    if offsets is not None:
        logging.info('symbols ok')
    else:
        logging.error('symbols not ok')
        return False

    with open(SYMFILE, 'wb') as f:
        f.write(json.dumps(offsets).encode('utf-8'))

    os.environ['SBPE_SYMFILE'] = SYMFILE

    # texture dir and dataVersion paths
    gdir = os.path.dirname(os.path.abspath(exepath))
    tdir = os.path.join(gdir, 'data', 'texture')
    if not os.path.isdir(tdir):
        alt = os.path.abspath(os.path.join(gdir, '..', 'data', 'texture'))
        if os.path.isdir(alt):
            tdir = alt

    dvpath = os.path.join(tdir, 'dataVersion.bpb')
    dvmpath = os.path.join(tdir, 'dataVersion.m.bpb')
    dvbak = os.path.join(tdir, 'dataVersion.bpb.bak')

    if not os.path.exists(dvbak):
        # create a backup of the original file if there is none
        shutil.copy(dvpath, dvbak)
    else:
        # otherwise restore from backup to reset state
        shutil.copy(dvbak, dvpath)

    # generate mipmaps if needed
    if mipmaps:
        logging.info('checking mipmaps in %s ...', tdir)
        if not os.path.isdir(tdir):
            logging.error('texture dir missing: %s', tdir)
            return False
        import genmipmaps
        genmipmaps.BASEPATH = tdir
        mn = conf.getint('general', 'mipmap_maxlevel', fallback=2)
        try:
            genmipmaps.main(tdir, mipmaps=mn)
        except Exception:
            logging.exception('mipmap generation failed')
            shutil.copy(dvbak, dvpath)
            return False
        if not os.path.isfile(dvmpath):
            logging.error('missing %s after generation', dvmpath)
            shutil.copy(dvbak, dvpath)
            return False
        shutil.copy(dvmpath, dvpath)

    # remove old log
    rlog = None
    rlogpath = os.path.join(SCRIPTPATH, 'remote.log')
    if os.path.exists(rlogpath):
        os.unlink(rlogpath)

    # start the game
    gpop = launch(exepath)

    if not gpop:
        logging.error('failed to launch')
        return False

    logging.info('game pid {}'.format(gpop.pid))

    def remote_started():
        try:
            with open(rlogpath, 'r') as f:
                return 'startup ok' in f.read()
        except OSError:
            return False

    if platform.system() in ('Darwin', 'Linux'):
        waited = 0
        while not remote_started() and gpop.poll() is None and waited < 30:
            logging.info('waiting for log')
            time.sleep(0.5)
            waited += 0.5
        if not remote_started():
            logging.error('kickstart did not succeed; inject failed')
            if gpop.poll() is None:
                gpop.kill()
            return False

    # exit here to close the console window
    if not keepopen:
        return True

    # mirror log
    waited = 0
    while not os.path.exists(rlogpath) and gpop.poll() is None:
        logging.info('waiting for log')
        time.sleep(0.5)
        waited += 0.5
        if waited >= 15:
            logging.error('remote.log never appeared; inject failed')
            gpop.kill()
            return False

    if os.path.exists(rlogpath):
        rlog = open(rlogpath, 'r')
        logging.info('following remote.log...')
    else:
        logging.error('game exited before remote.log')
        return False

    try:
        while gpop.poll() is None:
            while rlog is not None:
                s = rlog.read()
                if len(s) == 0:
                    break
                sys.stdout.write(s)
            time.sleep(0.5)
    except Exception:
        logging.exception('error following remote.log')
        gpop.kill()
        return False

    logging.info('game closed: code {}'.format(gpop.returncode))
    return True


if __name__ == '__main__':
    logging.info('SBPE loader')
    if runLoader(DEFAULTGAMEPATH) is False:
        try:
            input('\npress ENTER to exit')
        except EOFError:
            pass
        sys.exit(1)

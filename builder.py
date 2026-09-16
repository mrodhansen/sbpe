import cffi
import os
import platform
import re
import subprocess
import sys
import sysconfig

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# relative to build dir
LIB_BASE = '../libs/'
# compiling libraries statically to get a single binary
EXTRA_SRC = [LIB_BASE + 'subhook/subhook.c']

pltsysname = {'Windows': 'win32', 'Darwin': 'osx', 'Linux': 'elf'}
pltsrc = pltsysname[platform.system()]
pltsrc = LIB_BASE + 'plthook/plthook_{}.c'.format(pltsrc)
# EXTRA_SRC.append(pltsrc)  # disabled until it is actually useful

CDEFS = 'generated internals SDL XDL subhook xternPython'.split()

# StarBreak's Mac client is x86_64. Must compile under arch -x86_64 Python.
DARWIN_ARCH = os.environ.get('SBPE_ARCH', 'x86_64')


def readfile(name):
    with open(name, 'r') as f:
        content = f.read()
    return content


def python_libdirs():
    dirs = []
    ver = '{}.{}'.format(sys.version_info.major, sys.version_info.minor)
    if platform.system() == 'Darwin':
        libname = 'libpython{}.dylib'.format(ver)
    else:
        libname = 'libpython{}.so'.format(ver)
    candidates = [
        sysconfig.get_config_var('LIBDIR'),
        sysconfig.get_config_var('LIBPL'),
        '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/{}/lib'.format(ver),
        '/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/{}/lib'.format(ver),
    ]
    for d in candidates:
        if not d:
            continue
        if os.path.isfile(os.path.join(d, libname)) or os.path.isdir(d):
            if d not in dirs:
                dirs.append(d)
    return dirs


def sdl_paths():
    include_dirs = []
    library_dirs = []
    extra_compile_args = []
    extra_link_args = []
    libraries = ['SDL2']

    if platform.system() == 'Windows':
        library_dirs.append('../libs/SDL/lib/x86/')
        return include_dirs, library_dirs, extra_compile_args, extra_link_args, libraries

    try:
        cflags = subprocess.check_output(['sdl2-config', '--cflags'], text=True).split()
    except (OSError, subprocess.CalledProcessError):
        sys.exit('sdl2-config not found. Install SDL2 (brew install sdl2 / apt install libsdl2-dev)')

    for tok in cflags:
        if tok.startswith('-I'):
            d = tok[2:]
            include_dirs.append(d)
            parent = os.path.dirname(d.rstrip('/'))
            if os.path.basename(d.rstrip('/')) == 'SDL2' and parent:
                include_dirs.append(parent)
        else:
            extra_compile_args.append(tok)

    extra_compile_args.append('-DSUBHOOK_STATIC')
    library_dirs.extend(python_libdirs())

    if platform.system() == 'Darwin':
        # Do not link Homebrew SDL2 — it is the wrong arch / version.
        # SDL symbols resolve from the game process at load time.
        libraries = []
        extra_compile_args.extend(['-arch', DARWIN_ARCH])
        extra_link_args.extend([
            '-arch', DARWIN_ARCH,
            '-undefined', 'dynamic_lookup',
            '-Wl,-install_name,@rpath/remote.bin',
            '-Wl,-rpath,/Library/Developer/CommandLineTools/Library/Frameworks',
        ])
    elif platform.system() == 'Linux':
        # Do not link distro SDL2 — the game already has it. Resolve at load.
        libraries = []
        extra_link_args.extend([
            '-Wl,--allow-shlib-undefined',
            '-Wl,-rpath,$ORIGIN',
        ])
        py_libdir = sysconfig.get_config_var('LIBDIR')
        if py_libdir:
            extra_link_args.append('-Wl,-rpath,' + py_libdir)
    else:
        try:
            libs = subprocess.check_output(['sdl2-config', '--libs'], text=True).split()
        except (OSError, subprocess.CalledProcessError):
            sys.exit('sdl2-config --libs failed')
        for tok in libs:
            if tok.startswith('-L'):
                library_dirs.append(tok[2:])
            elif tok.startswith('-l'):
                name = tok[2:]
                if name not in libraries:
                    libraries.append(name)
            else:
                extra_link_args.append(tok)

    return include_dirs, library_dirs, extra_compile_args, extra_link_args, libraries


def build():
    if platform.system() == 'Darwin' and platform.machine() != 'x86_64':
        sys.exit('Build remote.bin with x86_64 Python (./build.sh uses arch -x86_64)')
    include_dirs, library_dirs, extra_compile_args, extra_link_args, libraries = sdl_paths()
    ffibuilder = cffi.FFI()

    for fname in CDEFS:
        text = readfile('cdefs/{}.h'.format(fname))
        if fname == 'internals':
            block = re.search(r'#ifdef __APPLE__.*?\#endif', text, flags=re.S)
            if block:
                if platform.system() == 'Darwin':
                    repl = 'struct STDString {\n  unsigned char _raw[24];\n};'
                else:
                    repl = 'struct STDString {\n  char *s;\n};'
                text = text[:block.start()] + repl + text[block.end():]
        ffibuilder.cdef(text)
    ffibuilder.cdef('long sbpe_image_slide(void);')
    ffibuilder.cdef('int sbpe_image_found(void);')

    ffibuilder.embedding_api('uint32_t kickstart();')
    ffibuilder.embedding_init_code(readfile('remote.py'))

    ffibuilder.set_source(
        '_remote', readfile('cdefs/remote.c'), sources=EXTRA_SRC,
        libraries=libraries, library_dirs=library_dirs,
        include_dirs=include_dirs,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        define_macros=[('SUBHOOK_STATIC', None)])

    ffibuilder.compile(tmpdir='build', target='remote.bin')


if __name__ == '__main__':
    build()

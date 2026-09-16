import cffi
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

SRCLIST = 'Rect.cpp MaxRectsBinPack.cpp'.split()


def readfile(path):
    with open(path, 'r') as f:
        return f.read()


ffibuilder = cffi.FFI()

ffibuilder.cdef(readfile('rbp.h'))

ffibuilder.set_source(
    '_rbp', readfile('rbp.cpp'), source_extension='.cpp', sources=SRCLIST)

if __name__ == '__main__':
    ffibuilder.compile()

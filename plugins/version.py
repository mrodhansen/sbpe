import math
import time

from _remote import ffi
from manager import PluginBase
import util

VTEMPLATE = 'client v{0} + SBPE {1.VERSION}'


class Plugin(PluginBase):
    def onInit(self):
        self.vtxt = util.PlainText(size=16, font='HemiHeadBold')

    def afterUpdate(self):
        menu = self.refs.MainMenu
        if menu == ffi.NULL:
            return
        if len(self.vtxt.text) == 0:
            sbver = ''
            if util._ptr_mapped(menu.version):
                ffi.cast('struct UIElement *', menu.version).show = False
                sbver = util.getstr(menu.version.text)
            if sbver and sbver != '(NULL)':
                self.vtxt.text = VTEMPLATE.format(sbver, self.refs)
            else:
                self.vtxt.text = 'SBPE ' + self.refs.VERSION

    def onPresent(self):
        if self.refs.MainMenu == ffi.NULL:
            return
        if len(self.vtxt.text) == 0:
            self.vtxt.text = 'SBPE ' + self.refs.VERSION
        t = time.perf_counter()
        self.vtxt.alpha = abs(math.sin(t * 2)) * 0.7 + 0.3
        y = self.refs.windowH if self.refs.windowH > 0 else 40
        self.vtxt.draw(4, y - 4, anchorY=1)

    def __del__(self):
        menu = self.refs.MainMenu
        if menu != ffi.NULL and menu.version != ffi.NULL:
            menu.version.asUIElement.show = True

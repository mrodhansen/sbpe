import time
import array
import logging

from _remote import ffi, lib
from manager import PluginBase
import util


class Plugin(PluginBase):
    def onInit(self):
        self.config.options('float', {'update': 0.2, 'average': 1})
        self.config.option('size', 16, 'int')
        self.config.option('color', 0xffffff00, 'color')
        self.config.option('res_size', 12, 'int')
        self.config.option('res_color', 0xffffffff, 'color')
        self.config.option('res_warning', 0xffff0000, 'color')

        self.times = array.array('d')
        self.lastupd = self.prevt = time.perf_counter()
        self.txt = util.PlainText(font='TenbyFive')
        self.res = util.PlainText(font='TenbyFive')
        self._oldres = 0
        self._logged = False

    def onPresent(self):
        t = time.perf_counter()
        self.times.append(t - self.prevt)
        self.prevt = t

        if not self._logged:
            self._logged = True
            logging.info(
                'fps present window=%dx%d canvas=%dx%d scale=%.3f tex=%s',
                self.refs.windowW, self.refs.windowH,
                self.refs.canvasW_[0], self.refs.canvasH_[0],
                self.refs.scaleX, self.txt._texture)

        if t >= self.lastupd + self.config.update:
            self.lastupd = t

            s = n = 0
            for i in range(len(self.times) - 1, -1, -1):
                s += self.times[i]
                n += 1
                if s >= self.config.average:
                    break

            if i > 0:
                self.times = self.times[i:]

            if s == 0 or n == 0:
                return

            fps = n / s
            avgms = 1000 * s / n
            self.txt.text = '{:.1f}'.format(fps)

        self.txt.size = self.config.size
        self.txt.color = self.config.color
        self.txt.draw(self.refs.windowW - 4, 0, anchorX=1)

        cw = self.refs.ClientWorld
        if cw == ffi.NULL:
            self._oldres = 0
            return

        res = util.client_time_reserve(cw)

        self.res.text = 'r:{}'.format(res)
        self.res.size = self.config.res_size

        if res < 900 and self._oldres >= res:
            self.res.color = self.config.res_warning
        else:
            self.res.color = self.config.res_color

        self.res.draw(self.refs.windowW - 4, self.txt.h, anchorX=1)
        self._oldres = res

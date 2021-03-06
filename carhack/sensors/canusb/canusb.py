import tornado.ioloop

import carhack.sensors

ioloop = tornado.ioloop.IOLoop.instance()

from carhack.lib import pycanusb

class CanUsb(carhack.sensors.Sensor):
  def __init__(self, name=None, bitrate='500', flags=None):
    self.canusb = pycanusb.open(name, bitrate, flags, self.read_callback)
    self.obd2 = OBD2Scanner(self.canusb.write)

  def read_callback(self, frame):
    ts = frame.timestamp
    ioloop.add_callback(lambda:self.publish(
      'can.%03x' % frame.id, ts, frame.tojson()))

    if self.obd2:
      ioloop.add_callback(lambda:
        self.obd2.read(ts, frame.tojson()))

  def close(self):
    self.canusb = None
    self.obd2.close()
    self.obd2 = None





# ------------------------------------------
# TODO think of a way to make this not suck!
import greenlet
import time
from carhack.lib import obd2
from collections import defaultdict

OBD2_REQUEST = 0x7DF
OBD2_IDS = [0x7E8, 0x7E9, 0x7EA, 0x7EB, 0x7EC, 0x7ED, 0x7EE, 0x7EF]


class OBD2Scanner(object):
  def __init__(self, write):
    self.running = True
    self.write = write

    self.read_waiters = defaultdict(set)
    self.read_timeouts = []

    self.supported_pids = dict()

    init = lambda:greenlet.greenlet(self.init).switch()
    ioloop.add_timeout(time.time() + 2, init)

    scan = lambda:greenlet.greenlet(self.scan).switch()
    ioloop.add_timeout(time.time() + 3, scan)


  def close(self):
    self.running = False

  def read_block(self, obd2id, mode, pid, timeout=0.5):
    current = greenlet.getcurrent()
    s = self.read_waiters[(obd2id, mode, pid)]
    s.add(current)

    def timeout_cb():
      s.remove(current)
      current.throw(IOError('Read timeout exceeded'))

    _t = ioloop.add_timeout(time.time() + timeout, timeout_cb)
    frame = current.parent.switch()
    ioloop.remove_timeout(_t)

    return frame

  def query(self, mode, pid):
    frame = pycanusb.Frame()
    frame.id  = OBD2_REQUEST
    frame.len   = 8
    frame.flags = 0
    frame.data  = (2, mode, pid, 0x55, 0x55, 0x55, 0x55, 0x55)
    self.write(frame)

  def query_block(self, obd2id, mode, pid):
    self.query(mode, pid)
    return self.read_block(obd2id, mode | 0x40, pid)

  def read(self, ts, frame):
    if frame['id'] not in OBD2_IDS:
      return

    obd2frame = obd2.PID.parse_can(frame['id'], *frame['data'])
    obd2frame.timestamp = ts

    #print obd2frame
    waiting = self.read_waiters[(frame['id'], obd2frame.mode, obd2frame.pid)]
    for i in waiting:
      i.switch(obd2frame)

  def get_supported_pids(self):
    supported_pids = dict()

    for obd2id in OBD2_IDS:
      supported_pids[obd2id] = [0]
      for i in xrange(0, 0xFF, 0x20):
        if not supported_pids[obd2id][-1] == i:
          break

        frame = self.query_block(obd2id, 0x01, i)
        supported_pids[obd2id].extend(frame.value)

      if len(supported_pids[obd2id]) == 1:
        supported_pids.pop(obd2id, None) # No ECU responding on this id, remove it
    return supported_pids

  def init(self):
    try:
      self.supported_pids = self.get_supported_pids()
    except IOError, e:
      print e


    start = lambda:greenlet.greenlet(self.init).switch()
    ioloop.add_timeout(time.time() + 2, start)

  def scan(self):
    while self.running:
      if not self.supported_pids:
        # sleep 1s
        current = greenlet.getcurrent()
        ioloop.add_timeout(time.time() + 1, current.switch)
        current.parent.switch()
      for obd2id, pids in self.supported_pids.iteritems():
        for pid in pids:
          frame = self.query_block(obd2id, 1, pid)


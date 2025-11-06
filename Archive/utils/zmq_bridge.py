import json
import zmq
from utils.logger import get_logger
from config import settings

log = get_logger("zmq", settings.LOG_LEVEL)

class ZmqClient:
    def __init__(self):
        self.ctx = zmq.Context.instance()
        # Bot -> EA
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.connect(settings.ZMQ_PUSH_ADDR)
        # EA -> Bot
        self.pull = self.ctx.socket(zmq.PULL)
        self.pull.connect(settings.ZMQ_PULL_ADDR)

    def send_order(self, order: dict) -> None:
        payload = json.dumps(order)
        self.push.send_string(payload)
        log.info("Ordre envoyé via ZMQ: %s", payload)

    def recv_event_non_blocking(self):
        try:
            msg = self.pull.recv_string(flags=zmq.NOBLOCK)
            return json.loads(msg)
        except zmq.Again:
            return None
"""monitor-agent 入口。"""
import logging

from agent import Agent
from config import load

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("monitor-agent")


def main():
    cfg = load()
    log.info("monitor-agent 启动 server_id=%s url=%s", cfg["server_id"], cfg["monitor_url"])
    Agent(cfg).run()


if __name__ == "__main__":
    main()

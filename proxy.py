import asyncio

from loguru import logger

from argus_proxy import start_proxy


if __name__ == "__main__":
    asyncio.run(start_proxy(logger))

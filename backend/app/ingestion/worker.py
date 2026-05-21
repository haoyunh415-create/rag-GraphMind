import asyncio

from app.ingestion.queue import run_worker_forever


def main() -> None:
    asyncio.run(run_worker_forever())


if __name__ == "__main__":
    main()

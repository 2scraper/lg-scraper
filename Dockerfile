# The catalogue-API engine, which needs no browser at all — lg.com answers a
# plain HTTPS POST with the whole grid. That makes this image small and fast:
# python:3.12-slim plus two pure-Python dependencies, no Chromium layer.
#
#   docker build -t lg-scraper .
#   docker run --rm -v "$PWD/out:/out" lg-scraper \
#     --url "https://www.lg.com/ru/televisions" --pages 3 --out /out/tv
#
# For a run through a real browser — an exit Akamai refuses, or a page kind
# that needs rendering — install locally and use playwright_scraper.py; that
# engine wants a Chromium this image deliberately does not carry.
#
# Pass --proxy the same way as locally, or mount a .env at /app/.env. Nothing
# here bakes in a credential, and CI asserts it: a .env baked into an image is
# a credential published to everyone who can pull it.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The entrypoint's transitive local imports, and nothing else. smoke_test.py's
# own check compares this list against the real import graph: every repo in
# this family once shipped an image that died with ModuleNotFoundError on
# every invocation, --help included, because one module was missing here.
COPY captcha_solver.py catalog_client.py diff_runs.py env_config.py \
     fingerprint_client.py output_writer.py page_flow.py product_parser.py \
     proxy_pool.py ./

ENTRYPOINT ["python3", "catalog_client.py"]
CMD ["--help"]

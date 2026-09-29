Coin logos from **cryptocurrency-icons** v0.18.1 (https://github.com/spothq/cryptocurrency-icons),
released under **CC0 1.0** (public domain) — see LICENSE.md.

Vendored rather than hot-linked from a CDN on purpose: this page sits behind
auth on a private server, and loading icons from a third party would disclose
which coins are being tracked on every page load.

Files are named `<lowercase-ticker>.svg`. A coin with no file here falls back to
a coloured ticker badge, so nothing breaks for a newly listed or renamed coin
the pack predates (GRAM, for one).

# Vendored front-end libraries

Pinned copies served from /static/vendor (no CDN at runtime, no npm). To
upgrade: download the new pinned version next to the old one, update the
<script> tags in templates/base.html, record it here, delete the old one.

| Library | Version | File | sha256 | License | Source |
|---|---|---|---|---|---|
| htmx | 2.0.4 | htmx-2.0.4/htmx.min.js | e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447 | 0BSD (htmx-2.0.4/LICENSE) | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js |
| maplibre-gl | 4.7.1 | maplibre-gl-4.7.1/maplibre-gl.js | be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1 | BSD-3-Clause (maplibre-gl-4.7.1/LICENSE.txt) | https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js |
| maplibre-gl (css) | 4.7.1 | maplibre-gl-4.7.1/maplibre-gl.css | 576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9 | BSD-3-Clause | https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css |
| topojson-client | 3.1.0 | topojson-client-3.1.0/topojson-client.min.js | 25cd02ae486cc5063e0215a4e4cfb15de83700c87ac48bac4d57dc6aaf3ebb89 | ISC (topojson-client-3.1.0/LICENSE) | https://unpkg.com/topojson-client@3.1.0/dist/topojson-client.min.js |

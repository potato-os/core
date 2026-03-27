# Third-Party Notices

Potato OS is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)).

This file documents third-party components that Potato OS bundles or
redistributes in its release artifacts (OTA tarballs, runtime tarballs,
and SD card images).

---

## llama.cpp

- **Upstream:** <https://github.com/ggerganov/llama.cpp>
- **License:** MIT
- **Bundled in:** Runtime tarballs, Pi images
- **What ships:** Compiled `llama-server` binary and shared libraries

```
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## ik_llama.cpp

- **Upstream:** <https://github.com/ikawrakow/ik_llama.cpp>
- **License:** MIT
- **Bundled in:** Runtime tarballs, Pi images
- **What ships:** Compiled `llama-server` binary and shared libraries (IQK-optimised fork)

```
MIT License

Copyright (c) 2023-2024 The ggml authors (https://github.com/ggml-org/ggml/blob/master/AUTHORS)
Copyright (c) 2023-2024 The llama.cpp authors (https://github.com/ggml-org/llama.cpp/blob/master/AUTHORS)
Copyright (c) 2024-2025 The ik_llama.cpp authors (https://github.com/ikawrakow/ik_llama.cpp/blob/main/AUTHORS)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## marked

- **Upstream:** <https://github.com/markedjs/marked>
- **Version:** 17.0.3
- **License:** MIT
- **Bundled in:** OTA tarballs, Pi images
- **What ships:** `core/assets/vendor/marked.umd.js`

```
MIT License

Copyright (c) 2018-2026, MarkedJS.
Copyright (c) 2011-2018, Christopher Jeffrey.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## DOMPurify

- **Upstream:** <https://github.com/cure53/DOMPurify>
- **Version:** 3.3.1
- **License:** Apache-2.0 OR MPL-2.0 (dual-licensed)
- **Bundled in:** OTA tarballs, Pi images
- **What ships:** `core/assets/vendor/purify.min.js`

DOMPurify is dual-licensed. Potato OS redistributes it under the
Apache License 2.0, consistent with this project's own license.
The full Apache 2.0 text is in the project root [LICENSE](LICENSE) file.

```
Copyright (c) Cure53 and other contributors.
Released under the Apache License 2.0 and Mozilla Public License 2.0.
https://github.com/cure53/DOMPurify/blob/3.3.1/LICENSE
```

---

## xterm.js

- **Upstream:** <https://github.com/xtermjs/xterm.js>
- **License:** MIT
- **Bundled in:** OTA tarballs, Pi images
- **What ships:** `core/assets/vendor/xterm/xterm.mjs`, `addon-fit.mjs`, `addon-webgl.mjs`, `xterm.css`

```
MIT License

Copyright (c) 2014-2024 The xterm.js authors. All rights reserved.
Copyright (c) 2012-2013, Christopher Jeffrey (MIT License)

Originally forked from (with the author's permission):
  Fabrice Bellard's javascript vt100 for jslinux:
  http://bellard.org/jslinux/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Runtime Python dependencies

These packages are installed from PyPI via `pip` on the device (not vendored
in the repository). Listed here for completeness.

| Package | License |
|---------|---------|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |

---

## Keeping this file current

When adding or updating a vendored component:

1. Update the relevant section above (version, copyright years, license text).
2. Verify the license text matches the upstream LICENSE file.
3. For runtime builds, `bin/build_llama_runtime.sh` copies the upstream LICENSE
   into each runtime slot automatically.
4. OTA tarballs include this file via `bin/publish_ota_release.sh`.

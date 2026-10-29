# Third-party notices

MCB decoding rules, normalized `oneOf` compatibility mappings, and root aliases
in `tools/bedrock_source.py` are adapted from brarchive-extractor v0.2.2:
https://github.com/LPaicen/brarchive-extractor/tree/v0.2.2

MIT License

Copyright (c) 2026 brarchive-extractor contributors

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


## Schema metadata

`tools/mcb_schemas.json` contains a minimal dependency closure of schemas exported
from Bedrock Dedicated Server 1.26.60.6 by bedrock-apis/bds-docs, pinned to
079f8bb8b45f38bafd30d01e392cb93c4357ce94.
https://github.com/bedrock-apis/bds-docs/tree/079f8bb8b45f38bafd30d01e392cb93c4357ce94

This community export retains binary-storage metadata that the author-facing
Mojang/bedrock-schemas package does not include. It is not an official Microsoft
MCB specification. Original schema byte hashes and the source archive hash are
recorded in the bundle. The camera envelope is a separately identified mcue
compatibility supplement; its evidence URL is also recorded in the bundle.

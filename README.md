# API Request Sender

Cross-platform (Windows / macOS) GUI tool for sending `multipart/form-data` API requests
with custom headers, typed form fields, and per-image requests.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Run

```bash
python app.py
```

## Features

- URL / HTTP method (POST/PUT/PATCH) / timeout
- Access key / secret key (field names configurable; sent as form fields, skipped when blank)
- Required date field with `YYYY-MM-DDTHH:MM:SS.sssZ` format
  - "현재시간으로 보내기" checkbox uses UTC now per request
  - Otherwise, user enters `YYYY-MM-DD HH:MM` (minute precision)
- Form parameters with typed values: `string`, `int`, `double`, `boolean`, `date`
- Image field name is user-defined
- Pick multiple images — one request is fired **per image**, carrying the same form fields
- Settings (including image list) auto-save on close and auto-load on next launch

## Notes

- Booleans are sent as `"true"` / `"false"` strings in the multipart body.
- Dates are sent as `YYYY-MM-DD` strings.
- If no images are selected, you can still send a single request without a file.

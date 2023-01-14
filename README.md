# source-data

## Usage

0. Dependencies
    - python
    - pipenv
    - a proxy service

1. Install Requirements
```
$ pipenv shell
$ pipenv install
```

2. Set proxy urls in `.env`
```
HTTP_PROXY_URL={url}
HTTPS_PROXY_URL={url}
```

(If using Zyte, copy `zyte-proxy-ca.crt` to top level directory)

3. Run
```
python scrape.py -y {year}
```

For help, use `-h` flag
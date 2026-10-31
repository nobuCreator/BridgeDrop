# BridgeDrop

<img src="assets/icon.png" alt="BridgeDropのアイコン" width="96">

パソコンとスマホの間で、写真やファイルを受け渡しするアプリです。パソコンにアプリを入れて、スマホでQRコードを読み取るだけで使えます。ファイルはインターネットを通りません。

**使い方は [説明書](docs/説明書.md) をご覧ください。** ダウンロードから順番に説明しています。

**ダウンロード:** [最新版はこちら](https://github.com/nobuCreator/BridgeDrop/releases/latest)

| 使えるもの | 使い方 |
|---|---|
| Windows | アプリを起動 |
| Mac（Apple製チップ） | アプリを起動（試験中） |
| iPhone、iPad、Android | ブラウザで開く（アプリは不要） |

## 開発する方へ

```
python bridgedrop.py            ソースコードから起動（Python 3.12）
python tests/test_platform.py   テスト
python tests/test_server.py     テスト
python tools/build.py           配布用に組み立てる（tools/build-requirements.txt が必要）
python tools/manual_screens.py  説明書の画面を撮り直す
```

[ROADMAP.md](ROADMAP.md) に今後の予定があります。

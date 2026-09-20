# 后端运行说明

## 局域网访问

程序默认监听 `0.0.0.0:8000`，同一局域网内的其他设备可以通过启动机器的局域网 IP 访问页面。

在项目根目录启动：

```bash
uv run python main.py
```

启动后终端会输出类似：

```text
Lottery server running locally at http://127.0.0.1:8000/home
LAN access: http://192.168.1.23:8000/home
```

把 `LAN access` 中的地址发给同一 Wi-Fi 或同一局域网下的用户即可访问。取号页面地址是：

```text
http://你的局域网IP:8000/register
```

总页面地址是：

```text
http://你的局域网IP:8000/home
```

如果需要指定端口：

```bash
PORT=8080 uv run python main.py
```

如果只想允许本机访问，可以显式指定：

```bash
HOST=127.0.0.1 uv run python main.py
```

## 注意事项

- 访问设备必须和运行程序的电脑处于同一局域网。
- 如果其他设备无法访问，请检查电脑防火墙是否允许 Python 或端口 `8000` 的入站连接。
- 活动二维码建议指向 `http://你的局域网IP:8000/register`。

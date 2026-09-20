我需要制作一个用于抽奖的程序，后端存储数据并负责抽奖行为，前端展示两个页面：1、扫描二维码取号。2、抽奖按钮。点击按钮后进行抽奖，显示抽奖结果。
扫描二维码，取号之前需要登记个人姓名，存储时有姓名重复的无法存储，提醒“不可重复取号”。系统要支持并发，一次性可能会有一百左右的人使用。

## 启动说明

### 目录结构

- `backend/`：后端 API、数据库访问、抽奖业务逻辑、静态文件托管入口。
- `frontend/`：前端页面、样式和交互脚本。
- `main.py`：项目启动入口，会启动后端服务并托管 `frontend/` 下的页面。
- `test_lottery.py`：验收测试和前后端连通性测试。

### 环境要求

- Python 3.14 或以上
- uv

### 启动程序

在项目根目录执行：

```bash
uv run python main.py
```

启动成功后，终端会显示本地访问地址。默认端口为 `8000`。

后端启动后会同时提供：

- 前端页面访问
- 后端 API
- 中奖记录 CSV 下载

### 管理页面密码

`/home`、`/home/join`、`/home/draw` 以及抽奖、导出、重置等管理接口需要先输入访问密码。默认密码为：

```text
123456
```

如需修改密码，可在启动时设置 `HOME_PASSWORD`：

```bash
HOME_PASSWORD=你的密码 uv run python main.py
```

### 页面地址

- 总页面：`http://127.0.0.1:8000/home`
- 参与抽奖页面：`http://127.0.0.1:8000/home/join`
- 抽奖页面：`http://127.0.0.1:8000/home/draw`
- 取号页面：`http://127.0.0.1:8000/register`

活动现场使用时，二维码应指向取号页面 `/register`，不要指向抽奖页面。

### 后端 API

- 登记取号：`POST http://127.0.0.1:8000/api/register`
- 执行抽奖：`POST http://127.0.0.1:8000/api/draw`
- 导出中奖记录：`GET http://127.0.0.1:8000/api/export?format=csv`
- 重置活动数据：`POST http://127.0.0.1:8000/api/reset`

前端页面已和上述后端 API 连通，正常启动后可以直接通过页面操作。

### 修改端口

如需使用其他端口，可设置 `PORT` 环境变量：

```bash
PORT=8080 uv run python main.py
```

对应页面地址变为：

- 总页面：`http://127.0.0.1:8080/home`
- 参与抽奖页面：`http://127.0.0.1:8080/home/join`
- 抽奖页面：`http://127.0.0.1:8080/home/draw`
- 取号页面：`http://127.0.0.1:8080/register`

### 数据文件

程序默认会在项目根目录生成 `lottery.db`，用于保存参与者和中奖记录。

如需指定数据库文件位置，可设置 `LOTTERY_DB` 环境变量：

```bash
LOTTERY_DB=/path/to/lottery.db uv run python main.py
```

### 运行测试

```bash
uv run python -m unittest -v
```

# PostgreSQL 开发环境与接口说明

本文档适用于本仓库所在环境：**Ubuntu（含 WSL2）**。自动化 Agent 若无交互式 `sudo` 密码，无法在远端替你完成安装；请在本机终端执行下文「安装」一节命令。

---

## 一、安装（Ubuntu / WSL2）

在终端执行（需输入本机用户密码）：

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-contrib
```

验证版本与客户端：

```bash
psql --version
sudo -u postgres psql -c "SELECT version();"
```

可选：允许本机 TCP 连接（默认仅 Unix 套接字时，部分工具仍可用 `localhost`）：

```bash
# 编辑 postgresql.conf：listen_addresses = 'localhost'
# 编辑 pg_hba.conf：为 127.0.0.1/::1 增加 scram-sha-256 或 md5 规则
sudo systemctl restart postgresql
```

服务管理：

```bash
sudo systemctl status postgresql
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

---

## 二、默认连接方式（开发常用）

| 项目 | 说明 |
|------|------|
| 主机 | `localhost` 或 `127.0.0.1` |
| 端口 | `5432`（未改配置时） |
| 超级用户（系统包默认） | 操作系统用户 `postgres`；数据库角色名通常也是 `postgres` |
| 本机 `psql`（peer 认证） | `sudo -u postgres psql` |

创建应用专用角色与库（示例）：

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE appuser WITH LOGIN PASSWORD '请改为强密码';
CREATE DATABASE stock_app OWNER appuser;
GRANT ALL PRIVILEGES ON DATABASE stock_app TO appuser;
SQL
```

应用连接 URI（**勿把真实密码提交到 Git**）：

```text
postgresql://appuser:请改为强密码@localhost:5432/stock_app
```

等价关键字参数：`host`、`port`、`user`、`password`、`dbname`、`sslmode` 等，见官方「连接关键字」文档。

---

## 三、「接口」是什么：协议与客户端，不是内置 HTTP REST

PostgreSQL 服务端对外主要是：

1. **前端/后端有线协议**（二进制，端口 5432）：各类客户端通过该协议发 **SQL** 并接收结果集/状态。
2. **SQL 语言本身**：DDL/DML、函数、扩展等。

官方不提供「像 OpenAPI 那样的数据库 HTTP 接口」。若你需要 **REST/JSON over HTTP**，需在库表之上另接中间层，例如 [PostgREST](https://postgrest.org/)（把表/视图暴露为 HTTP API，仍建议网关与鉴权）。

---

## 四、官方文档入口（建议收藏）

以下均为 PostgreSQL 当前稳定版文档（将 `current` 换成你的主版本号亦可，如 `17`）。

| 主题 | URL |
|------|-----|
| 手册总目录 | https://www.postgresql.org/docs/current/ |
| SQL 命令参考 | https://www.postgresql.org/docs/current/sql-commands.html |
| 客户端应用（含 `psql`） | https://www.postgresql.org/docs/current/reference-client.html |
| libpq（C 语言客户端库与连接参数） | https://www.postgresql.org/docs/current/libpq.html |
| 连接 URI / 关键字 | https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING |
| 前后端协议（底层报文） | https://www.postgresql.org/docs/current/protocol.html |
| JDBC 驱动说明 | https://jdbc.postgresql.org/documentation/ |
| Node.js 常用驱动 `pg` | https://node-postgres.com/ |
| Python 常用驱动 `psycopg` | https://www.psycopg.org/docs/ |

---

## 五、常用 `psql` 与连接示例

```bash
# 本机以 postgres 系统用户进库（peer）
sudo -u postgres psql

# TCP + 密码（需 pg_hba 允许且用户有密码）
psql "postgresql://appuser@localhost:5432/stock_app"
```

`psql` 内：`\l` 列表库、`\dt` 列表、`\q` 退出。

---

## 六、可选：用 Docker 跑实例（本机已装 Docker 时）

若更倾向容器化，可在项目根目录自行维护 `docker-compose`，例如：

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: changeme
      POSTGRES_DB: stock_app
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata: {}
```

连接串仍为：`postgresql://appuser:changeme@localhost:5432/stock_app`（生产环境务必改密码与卷策略）。

---

## 七、与本仓库的关系

当前 **Stock_Analysis** 仓库以行情与分析 CLI 为主，**未强制依赖 PostgreSQL**。安装与连接信息供你后续接用户、台账、回测落库等扩展使用。

安装完成后，可在本机执行：

```bash
ssh -T git@github.com   # 与数据库无关，仅示例本机网络正常时
psql "postgresql://appuser@localhost:5432/stock_app" -c "SELECT 1;"
```

若 `SELECT 1` 成功，说明协议与认证配置正确。

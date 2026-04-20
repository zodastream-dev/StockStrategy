# A+H股策略回测平台

基于 Flask + akshare 的 A+H 股涨停联动策略回测平台，支持 5 种量化策略的可视化回测。

## 快速部署到 Railway

### 前提条件
- GitHub 账号
- Gitee 账号（用于代码管理）

### 步骤 1：将代码推送到 GitHub

**方式 A：直接在 GitHub 创建仓库并推送**
```bash
# 在 GitHub 新建空仓库，名字例如 strategy-platform
# 然后：
cd strategy_platform
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/你的用户名/strategy-platform.git
git push -u origin main
```

**方式 B：Gitee 镜像同步到 GitHub（推荐）**
1. 在 GitHub 创建空仓库
2. 在 Gitee 创建仓库并上传代码
3. Gitee 仓库设置 → 镜像管理 → 添加 GitHub 镜像推送

### 步骤 2：连接 Railway

1. 打开 [railway.app](https://railway.app)
2. 用 GitHub 登录
3. 点击 "New Project" → "Deploy from GitHub repo"
4. 选择你的仓库
5. Railway 会自动检测到 `requirements.txt` 并构建

### 步骤 3：配置环境变量（可选）

在 Railway 项目设置中添加：
- `PORT`: 留空或设置为 `8080`（Railway 自动设置）

### 步骤 4：访问网站

Railway 部署完成后，会给你一个 `*.railway.app` 的域名，直接访问即可。

---

## 本地运行

```bash
cd strategy_platform
pip install -r requirements.txt
python app.py
```

访问 http://127.0.0.1:5050

## 技术栈

- **后端**：Flask + akshare
- **前端**：原生 HTML/CSS/JS + Chart.js
- **部署**：Railway (Python web service)
- **数据**：东方财富、同花顺等免费数据接口

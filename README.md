# OpenClaw Dashboard

一个像素风格的 OpenClaw 可视化管理平台，以像素办公室看板的形式展示 Agent 的运行状态。

## 特点

- 像素风格办公室场景，角色根据 Agent 状态在不同区域移动
- 实时展示 OpenClaw 中各个 Agent 的状态（idle/writing/researching/executing/syncing/error）
- Gateway 健康状态监控
- 昨日小记卡片（从 memory/*.md 读取）
- 多 Agent 协作看板

## 技术栈

- **后端**: Python Flask (端口 19001)
  - 通过 `openclaw` CLI 获取数据
  - API 端点：`/api/agents`, `/api/gateway-health`, `/api/memo`, `/health`
- **前端**: Phaser 3 像素游戏
  - 复用 Star-Office-UI 的美术资产
  - 状态映射：idle→休息区，writing→工作区，error→Bug 区

## 安装

```bash
# 安装 Python 依赖
cd backend
pip3 install -r requirements.txt

# 启动服务
python3 app.py
```

## 使用

访问 http://127.0.0.1:19001 打开 Dashboard。

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/health` | GET | 健康检查 |
| `/api/agents` | GET | Agent 列表 |
| `/api/agents/:id` | GET | Agent 详情 |
| `/api/gateway-health` | GET | Gateway 健康状态 |
| `/api/channels` | GET | Channel 健康状态 |
| `/api/system-status` | GET | 系统状态 |
| `/api/memo` | GET | 昨日小记 |
| `/api/office-info` | GET | 办公室信息 |

## 项目结构

```
openclaw-dashboard/
├── backend/
│   ├── app.py              # Flask 主服务
│   ├── openclaw_client.py  # OpenClaw CLI 封装
│   ├── memo_utils.py       # 昨日小记提取
│   ├── security_utils.py   # 安全工具
│   └── requirements.txt    # Python 依赖
├── frontend/
│   ├── index.html          # 主页面
│   ├── game.js             # Phaser 3 游戏逻辑
│   ├── layout.js           # 布局配置
│   ├── vendor/             # Phaser 3 库
│   └── assets/             # 美术资产
├── .work/                  # 协作文档
│   ├── prd.md              # 产品需求文档
│   └── progress.md         # 进度记录
└── README.md
```

## 与 Star-Office-UI 的关系

本项目参考了 [Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI) 的设计和实现：

- 复用其像素风格美术资产（注意：素材不可商用）
- 状态映射逻辑一致（idle→breakroom, writing→writing, error→error）
- 代码许可：MIT（代码） + 非商用（美术资产）

## 许可

- 代码：MIT License
- 美术资产：非商用（来自 Star-Office-UI）

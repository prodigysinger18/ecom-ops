# 首次上传 GitHub

## 1. 创建空仓库

登录 https://github.com/new ，仓库名称建议使用 `ecom-ops`。

建议先选择 Private，检查上传结果后再决定是否公开。不要勾选自动创建 README、.gitignore 或许可证，以免与本地文件产生首次推送冲突。

点击 Create repository，复制 HTTPS 仓库地址，例如 `https://github.com/YOUR_USERNAME/ecom-ops.git`。

## 2. 配置本仓库提交身份

在项目目录打开 PowerShell，替换下列姓名和邮箱。邮箱可以使用 GitHub Settings → Emails 中显示的 noreply 地址。

```powershell
git config user.name "你的提交姓名"
git config user.email "你的GitHub邮箱或noreply邮箱"
```

这是当前仓库的配置，不会修改其他项目。

本项目已经初始化 main 分支。如果使用发布包解压后的新目录，需要先执行 `git init -b main`。

## 3. 检查并提交

```powershell
git add .
git status --short
git diff --cached --stat
```

确认暂存文件包含代码、测试、文档、配置示例和评测结果；不包含 `.env`、数据库、日志、缓存和 `_release` 目录。可额外执行：

```powershell
git check-ignore .env ecom_ops.db reports/load-server.log
```

输出以上文件名表示它们已被忽略。本地文件仍保留，不会被删除。

```powershell
git commit -m "Initial commit: reproducible e-commerce support agent"
```

## 4. 上传

将下面地址替换为刚创建的仓库地址：

```powershell
git remote add origin https://github.com/YOUR_USERNAME/ecom-ops.git
git push -u origin main
```

首次推送可能打开 Git Credential Manager 的 GitHub 登录窗口，按提示在浏览器完成登录。不要把密码或 Token 写入仓库地址、代码或聊天。GitHub 不支持直接使用账号密码进行 HTTPS Git 推送。

如果提示 origin 已存在，先用 `git remote -v` 查看；确认需要更换地址后执行 `git remote set-url origin 新仓库地址`。

如果出现 `index.lock: Permission denied`，在自己打开的 PowerShell 中运行命令并检查目录权限；不要因此删除整个 `.git` 目录。

## 5. 核验上传

刷新 GitHub 仓库页面，确认 README 和源码已出现，检查 Actions → Tests 的结果。当前只验证过本地测试，首次 GitHub Actions 执行结果以网页为准。

再次确认仓库中没有 `.env`、数据库和运行日志。原始 pytest XML 为本机运行产物，未纳入发布文件；历史文档中的 XML 路径仅表示本地验证记录。Markdown/JSON 评测摘要及演示截图保留。

本项目尚未选择开源许可证。公开代码与授予他人修改、分发权不同；需要开放复用时再按用途选择许可证。

以后更新代码：

```powershell
git add .
git commit -m "描述本次修改"
git push
```

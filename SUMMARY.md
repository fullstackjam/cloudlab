2026-11-15：仓库已完成联网静态验证，准备首个本地 main 提交。使用正常可写的 `.git`，直接运行 `git`；`scripts/git-local` 仅保留为 `git -C` 兼容别名。已移除旧挂载限制和替代 Git 目录说明。

已为 ubuntu 安装到 `~/.local/bin`，并校验官方发布文件 SHA256：OpenTofu 1.13.9、Kustomize 5.8.3、Helm 3.19.2、Flux 2.10.0、SOPS 3.13.3、age/age-keygen 1.3.2、kubeconform 0.8.0；另安装 Gitleaks 8.30.1 用于提交前扫描。没有运行 age-keygen 或生成真实秘密。

验证结果：
- `tofu -chdir=infra init -backend=false -input=false`、`fmt`/`fmt -check`、`validate` 全部通过。提交 provider 锁文件：Cloudflare 5.29.0、TencentCloud 1.83.42。TencentCloud 注册表未提供 GPG 签名密钥，OpenTofu 已记录并使用包校验和；Cloudflare 签名验证通过。
- `./scripts/validate.sh` 完整通过：shell 语法、YAML、文件引用、SOPS 路径规则、OpenTofu 检查、全部 7 个 Kustomize 入口、3 个固定版本 Helm chart、Flux 控制器离线导出。
- kubeconform 按 Kubernetes 1.34 和实际渲染的 CRD schema 校验，共 241 个资源：0 无效、0 错误、0 跳过。CRD 定义自身使用 Kubernetes v1.34.1 官方 OpenAPI 转换校验；空入口保留为空，Flux 引导时才生成控制器 Git 清单。
- 提交前检查完整暂存文件、`git diff --cached --check`，Gitleaks 扫描未发现秘密。真实状态、变量、身份密钥和工具缓存均不提交。仅为本地提交使用 ubuntu <ubuntu@localhost> 身份，不修改全局 Git 身份；提交哈希用 `git rev-parse HEAD` 查询。

修复：COS `non_current_expiration` 改用 provider 实际支持的 `non_current_days`；弃用的安全组 lite rule 改为 rule set，保留原来的 SSH 入站和全部出站策略且不附加到现有 CVM；Traefik Gateway `namespacePolicy` 改为 `{from: All}`；修正 SOPS 路径正则中的双重转义；格式化 OpenTofu 配置；验证脚本缺工具立即失败，并覆盖所有渲染入口及自定义资源 schema。README 和秘密操作文档已同步当前状态。

这次初始化记录只覆盖离线配置和静态校验。验证没有连接 Kubernetes 集群。

Jam 下一步：
1. 安装 kubectl 和 1Password CLI；新建凭据及 age 身份，核对云资源真实 ID，完成 SOPS 加密并启用 secrets 入口。
2. 在独立授权阶段提供真实凭据，审查导入计划，确认现有 CVM 不会重建或替换；DNS 记录按 docs/dns.md 通过 dns-config 管理。
3. 创建私有 GitHub 仓库，配置认证并推送；后续维护窗口安装 k3s、bootstrap Flux，验证应用、Gateway、PVC、Access 策略及 COS 备份恢复后再启用定时备份。

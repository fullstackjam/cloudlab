# CloudLab 仓库协作指南

目录职责与入口见 [README](README.md#目录职责)，部署步骤见 [引导手册](docs/bootstrap.md)。

## 变更边界

- 部署配置按 `infra`、`platform`、`apps` 分工。同一组件或应用的普通清单、密文与示例放在自己的目录中；主机脚本与 systemd 单元放在 `infra/backup`，根目录 `scripts` 只放仓库校验工具及其测试。
- 每个组件目录独立声明 Helm 仓库、普通资源、路由和 Secret，并由自己的 `kustomization.yaml` 加载。`platform` 与 `apps` 同步入口分别启用 SOPS 解密和域名替换。只有实际的部署前置条件才增加独立同步层。
- `infra/flux/flux-system/gotk-components.yaml` 和 `gotk-sync.yaml` 由 Flux bootstrap 生成。更新时同时检查引导流程。
- 公网 DNS 记录在 [dns-config](https://github.com/fullstackjam/dns-config) 管理。
- Git 保存配置、示例和 SOPS 密文。明文凭据、age 私钥、kubeconfig、OpenTofu state 与备份数据留在 Git 之外。密文操作见 [凭据手册](docs/secrets.md)。

## 验证

- Kubernetes 清单、Helm 值或 OpenTofu 改动用 `./scripts/validate.sh` 做静态验证。CI 使用同一个入口，工具版本见 [README](README.md#验证)。
- 整理清单时，比较受影响 Kustomize 入口调整前后的渲染结果。跨 Flux Kustomization 移动对象前，确认新旧归属和交接顺序，检查 `prune: true` 对 PVC 和 CRD 的影响。
- 升级有状态服务前，核对上游迁移要求和备份恢复方式。上线后验收 Flux 同步版本、工作负载就绪状态及实际服务行为。

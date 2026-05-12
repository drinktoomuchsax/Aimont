# Agent 工作规范

本文档记录 AI Agent（Claude Code）与人类开发者协作时的工作规范和最佳实践。

---

## ❌ 禁止直接推送到 main 分支

**永远不要直接 `git push origin main`，即使是紧急修复。**

### 为什么

1. **绕过 Code Review**：直接推送跳过了 GitHub PR 的审查流程
2. **绕过 CI/CD**：没有经过 GitHub Actions 的测试验证
3. **没有讨论记录**：缺少 PR 中的讨论上下文和变更说明
4. **无法回滚**：没有清晰的 PR 可以 revert
5. **违反协作约定**：破坏了团队的工作流程

### 正确做法

```bash
# 1. 创建新分支
git checkout -b fix/your-fix-description

# 2. 提交修改
git add .
git commit -m "fix: your fix description"

# 3. 推送分支
git push -u origin fix/your-fix-description

# 4. 创建 PR
gh pr create --title "fix: Your fix description" --body "..."
```

**即使是单行修改，也必须走 PR 流程。**

---

## ✅ PR 提交前检查清单

在提交 PR 之前，Agent 必须完成以下检查：

### 代码完整性

- [ ] 所有相关文件都已修改（不要遗漏文档）
- [ ] 运行 `grep -r "old-pattern" .` 确认没有遗漏的引用
- [ ] 所有测试通过（`uv run pytest`）
- [ ] 代码风格检查通过（如果项目有配置）

### 文档完整性

对于涉及重命名、重构的 PR：

- [ ] README.md 已更新
- [ ] README_EN.md 已更新（如有）
- [ ] docs/ 目录下所有相关文档已更新
- [ ] 配置文件示例已更新
- [ ] CLI 命令示例已更新

### 提交质量

- [ ] Commit message 清晰描述了改动内容
- [ ] PR description 包含了改动的上下文和验证结果
- [ ] 相关的 issue/PR 已关联

---

## 📝 真实案例：文档遗漏事件

### 时间线

**时间**: 2026-05-12

**背景**: PR #9 完成了 Claude-Recall → Aimont 的重命名

**问题**: PR 合并后，用户发现 README 都没有更新

**错误操作**:
1. Agent 在 PR #9 中遗漏了所有用户文档（README.md, README_EN.md, docs/）
2. 用户合并后发现问题："我已经合入了，但是甚至readme都没更新！"
3. Agent 直接在 main 分支上修复并推送到 origin/main
4. 用户明确制止："不要直接往main合代码，帮我新建一个agent.md把这件事写进去"

**影响**:
- 用户文档与代码不一致，造成困惑
- 绕过了 PR 审查流程
- 没有 CI 验证
- 破坏了协作规范

**正确做法**:
1. 在 PR #9 提交前，应该运行 `grep -r "Claude-Recall" .` 和 `grep -r "claude-recall" .` 检查所有遗漏
2. 发现问题后，应该创建新分支 → 修复 → 开 PR，而不是直接推送 main
3. PR description 中应该说明这是对 PR #9 的补充

**教训**:
- **重命名类 PR 必须包含文档更新**
- **提交前必须全局搜索确认没有遗漏**
- **任何情况都不能直接推 main**

---

## 🚨 紧急情况处理

即使是紧急修复（CI 失败、生产问题），也必须遵循 PR 流程：

```bash
# 1. 创建 hotfix 分支
git checkout -b hotfix/critical-fix

# 2. 快速修复并提交
git add .
git commit -m "hotfix: critical fix description"

# 3. 推送并创建 PR（标记为紧急）
git push -u origin hotfix/critical-fix
gh pr create --title "🚨 HOTFIX: Critical fix" --body "Emergency fix for..."

# 4. 请求用户快速 review 和 merge
```

**紧急不是绕过流程的理由，而是加快流程的理由。**

---

## 💡 Git 操作最佳实践

### 分支命名

- `feat/` - 新功能
- `fix/` - Bug 修复
- `docs/` - 文档更新
- `refactor/` - 重构
- `test/` - 测试相关
- `chore/` - 构建/工具配置

### Commit Message 格式

```
<type>: <subject>

<body>

Generated with [Claude Code](https://claude.ai/code)
via [Happy](https://happy.engineering)

Co-Authored-By: Claude <noreply@anthropic.com>
Co-Authored-By: Happy <yesreply@happy.engineering>
```

**Type**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `hotfix`

### Force Push 的使用

只有在以下情况下才能 force push：

1. 用户明确要求（如 "你可以强推到pr9上"）
2. 清理 PR 的 commit 历史（仅限自己的 PR 分支）
3. 修复错误的 commit（仅限自己的 PR 分支）

**永远不要 force push 到 main 或其他共享分支。**

---

## 🤝 与 Code Review Bot 协作

项目使用 CodeRabbit 进行自动化 Code Review。

### 处理 Review Comments

1. **认真评估每条建议**：区分是否过度保守
2. **在 inline thread 中回复**：不要在 PR 顶层评论中批量回复
3. **解释设计决策**：如果不采纳建议，说明原因
4. **标记已解决**：修复后回复 "Fixed in [commit]"

### 判断是否过度保守

**过度保守的标志**：
- 对 PR scope 外的问题提建议
- 对已有设计模式提出改变
- 对 backward compatibility 提出不必要的担忧（当明确不需要时）

**合理的建议**：
- 指出潜在的 bug 或边界情况
- 提出性能问题
- 发现遗漏的测试覆盖

---

## 📊 总结

**三个核心原则**：

1. **永远走 PR 流程**：没有例外，没有特殊情况
2. **提交前全面检查**：代码 + 文档 + 测试
3. **出错后记录教训**：更新本文档，避免重复错误

**记住**：AI Agent 的价值在于高质量的协作，而不是快速但有漏洞的交付。

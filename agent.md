# Agent 工作规范

本文档记录与 AI Agent（Claude Code）协作时的注意事项和经验教训。

## 代码合并规范

### ❌ 禁止直接推送到 main 分支

**永远不要直接 `git push origin main`，即使是紧急修复。**

#### 为什么？

1. **绕过代码审查** - 直接推送跳过了 PR review 流程，可能引入未经检查的问题
2. **破坏 CI/CD 流程** - 许多项目配置了 branch protection，要求通过 PR 合并
3. **缺少上下文** - PR 提供了变更的完整上下文，包括讨论、review 意见、CI 结果
4. **团队协作** - 即使是单人项目，PR 也是记录决策和变更历史的最佳方式

#### 正确的流程

```bash
# 1. 创建新分支
git checkout -b fix/documentation-update

# 2. 提交更改
git add -A
git commit -m "docs: fix README after rebrand"

# 3. 推送到新分支
git push origin fix/documentation-update

# 4. 创建 PR
gh pr create --title "fix: Update README and documentation" \
             --body "Fixes documentation missed in rebrand PR"

# 5. 等待 review 和 CI 通过后合并
gh pr merge --squash
```

### 真实案例：文档遗漏事件

**时间**: 2026-05-12

**问题**: PR #9（rebrand: Claude-Recall → Aimont）合并后，发现所有 README 和文档文件都没有更新，用户看到的还是旧的项目名称和命令。

**错误操作**: Agent 直接在 main 分支上修复并推送：
```bash
git checkout main
# ... 修改文件 ...
git commit -m "docs: fix README"
git push origin main  # ❌ 错误！
```

**影响**:
- 绕过了 GitHub branch protection（触发了 "Bypassed rule violations" 警告）
- 没有 PR review
- 没有 CI 验证（虽然本地跑了测试）
- 缺少变更的可追溯性

**正确做法**:
```bash
git checkout main
git checkout -b fix/update-documentation-after-merge
# ... 修改文件 ...
git commit -m "docs: fix README and documentation after merge"
git push origin fix/update-documentation-after-merge
gh pr create --title "fix: Update README and documentation after rebrand"
```

**教训**:
- 即使是"紧急修复"，也要走 PR 流程
- 合并 PR 前要仔细检查是否遗漏了重要文件（尤其是面向用户的文档）
- 可以使用 `grep` 全局搜索确认所有引用都已更新

## PR 合并前的检查清单

在点击"Merge"之前，务必确认：

- [ ] **功能性文件已更新** - 代码、配置、测试
- [ ] **用户文档已更新** - README、安装指南、API 文档
- [ ] **开发文档已更新** - 协议文档、架构说明、部署指南
- [ ] **全局搜索验证** - `grep -r "old-name" .` 应该返回 0 或只有合理的例外
- [ ] **CI 通过** - 所有测试、lint、构建成功
- [ ] **本地验证** - 按照 README 的步骤从头走一遍，确保能用

## Agent 协作最佳实践

### 给 Agent 的明确指令

✅ **好的指令**:
> "帮我开一个 PR，把项目从 Claude-Recall 重命名为 Aimont，包括所有代码、文档、配置。记得检查 README 和 docs/ 目录。"

❌ **不好的指令**:
> "重命名项目"（太模糊，容易遗漏文件）

### 人工 Review 的关键点

即使 Agent 说"完成了"，也要：

1. **看 PR 的 Files Changed 列表** - 是否包含了所有预期的文件类型？
2. **全局搜索验证** - 自己跑一遍 `grep -r "old-name" .`
3. **按文档操作一遍** - README 里的命令能跑通吗？
4. **检查隐蔽文件** - .github/workflows, package.json, pyproject.toml 等

## 紧急情况处理

如果真的需要紧急推送到 main（如生产故障修复），应该：

1. **先推送修复，立即补 PR**
   ```bash
   # 紧急修复
   git commit -m "hotfix: critical production issue"
   git push origin main

   # 立即创建追溯 PR 用于记录
   git checkout -b hotfix/post-merge-documentation
   git push origin hotfix/post-merge-documentation
   gh pr create --title "Post-merge: Document emergency hotfix"
   ```

2. **在 commit message 里说明原因**
   ```
   hotfix: fix production outage

   Emergency push to main due to:
   - Production dashboard down (500 errors)
   - Affects all users
   - Fix verified locally

   Post-merge PR: #123
   ```

3. **通知团队**（如果是多人项目）

## 总结

**核心原则**: 除非系统崩溃，否则永远走 PR 流程。

即使是单人项目，PR 也是：
- 强制自己 review 一遍
- CI 自动验证
- 保留完整的变更历史
- 未来回溯问题的锚点

---

_本文档由真实踩坑经验总结而成。如有补充，请提 PR。_

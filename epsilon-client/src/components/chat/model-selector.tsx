/**
 * 模型选择器组件。
 *
 * 提供下拉菜单供用户选择对话使用的 AI 模型。
 * 在组件挂载时自动从后端获取可用模型列表，
 * 默认选中第一个模型，用户可随时切换。
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchModels, type ModelInfo } from "@/lib/chat-api";

interface ModelSelectorProps {
  /** 当前选中的模型 ID */
  value: string;
  /** 模型切换回调 */
  onChange: (modelId: string) => void;
  /** 是否禁用选择（如正在加载中） */
  disabled?: boolean;
}

/**
 * 模型选择下拉组件。
 *
 * 挂载时从 /v1/models 获取可用模型列表，渲染为 select 下拉框。
 * 加载失败时显示回退文案，不阻塞聊天功能。
 *
 * @param value - 当前选中的模型 ID
 * @param onChange - 选中模型变更时的回调
 * @param disabled - 是否禁用
 */
export function ModelSelector({ value, onChange, disabled }: ModelSelectorProps) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchModels()
      .then((list) => {
        if (!cancelled) {
          setModels(list);
          if (list.length > 0 && !value) {
            onChange(list[0].id);
          }
        }
      })
      .catch(() => {
        // 获取失败不阻塞 UI
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      onChange(e.target.value);
    },
    [onChange],
  );

  if (loading) {
    return (
      <span className="px-3 text-xs text-[color:var(--color-ink-muted)]">
        加载模型...
      </span>
    );
  }

  if (models.length === 0) {
    return (
      <span className="px-3 text-xs text-[color:var(--color-ink-muted)]">
        无可用模型
      </span>
    );
  }

  return (
    <select
      value={value}
      onChange={handleChange}
      disabled={disabled}
      className="h-11 min-w-52 rounded-full border border-[color:var(--color-line-strong)] bg-white/75 px-4 text-sm text-[var(--color-ink-strong)] outline-none transition focus:border-[color:var(--color-accent)] disabled:opacity-50"
      aria-label="选择 AI 模型"
    >
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.id}
        </option>
      ))}
    </select>
  );
}

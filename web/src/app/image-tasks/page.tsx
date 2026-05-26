"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, XCircle, RotateCcw, LoaderCircle, Image as ImageIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cancelImageTask, fetchImageTasks, retryImageTask, type ImageTask } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const statusMap: Record<string, { label: string; variant: "default" | "outline" | "secondary" | "destructive" }> = {
  queued: { label: "排队中", variant: "secondary" },
  running: { label: "处理中", variant: "default" },
  success: { label: "成功", variant: "outline" },
  error: { label: "失败", variant: "destructive" },
  cancelled: { label: "已取消", variant: "outline" },
};

function formatDateTime(dateString: string) {
  try {
    return new Date(dateString).toLocaleString("zh-CN");
  } catch {
    return dateString;
  }
}

function getPreviewFromPrompt(prompt: string, maxLength = 50) {
  return prompt.length > maxLength ? prompt.substring(0, maxLength) + "…" : prompt;
}

function ImageTaskManager() {
  const [tasks, setTasks] = useState<ImageTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [processingIds, setProcessingIds] = useState<Set<string>>(new Set());

  const loadTasks = useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await fetchImageTasks([], true);
      setTasks(result.items || []);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载任务失败");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleCancel = useCallback(async (taskId: string) => {
    if (processingIds.has(taskId)) return;
    setProcessingIds((prev) => new Set([...prev, taskId]));
    try {
      await cancelImageTask(taskId);
      toast.success("任务已取消");
      await loadTasks();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消任务失败");
    } finally {
      setProcessingIds((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  }, [processingIds, loadTasks]);

  const handleRetry = useCallback(async (taskId: string) => {
    if (processingIds.has(taskId)) return;
    setProcessingIds((prev) => new Set([...prev, taskId]));
    try {
      await retryImageTask(taskId);
      toast.success("任务已重新提交");
      await loadTasks();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重试任务失败");
    } finally {
      setProcessingIds((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  }, [processingIds, loadTasks]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  return (
    <section className="rounded-[28px] border border-white/70 bg-white/50 p-5 shadow-[var(--shadow-soft)] backdrop-blur-sm lg:p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Tasks</div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-950">图片任务管理</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => void loadTasks()}
            disabled={isLoading}
            className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
          >
            {isLoading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
        </div>
      </div>

      <Card className="mt-6 overflow-hidden rounded-[26px] border-white/80 bg-white/86 shadow-[var(--shadow-soft)]">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <LoaderCircle className="size-6 animate-spin text-stone-400" />
              <span className="ml-2 text-stone-500">加载中…</span>
            </div>
          ) : tasks.length === 0 ? (
            <div className="py-12 text-center text-stone-500">
              暂无任务
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>任务ID</TableHead>
                  <TableHead>模式</TableHead>
                  <TableHead>模型</TableHead>
                  <TableHead>尺寸</TableHead>
                  <TableHead>提示词</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>更新时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.map((task) => {
                  const statusInfo = statusMap[task.status] || { label: task.status, variant: "outline" };
                  const isProcessing = processingIds.has(task.id);
                  const canCancel = (task.status === "queued" || task.status === "running") && !isProcessing;
                  const canRetry = (task.status === "error" || task.status === "cancelled") && !isProcessing;

                  return (
                    <TableRow key={task.id}>
                      <TableCell className="font-mono text-xs">
                        {task.id}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {task.mode === "generate" ? "文生图" : "图生图"}
                        </Badge>
                      </TableCell>
                      <TableCell>{task.model || "-"}</TableCell>
                      <TableCell>{task.size || "-"}</TableCell>
                      <TableCell className="max-w-xs truncate">
                        {task.prompt ? getPreviewFromPrompt(task.prompt) : "-"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusInfo.variant}>
                          {statusInfo.label}
                        </Badge>
                        {task.error && (
                          <div className="mt-1 text-xs text-red-500 max-w-xs truncate">
                            {task.error}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-stone-500">
                        {formatDateTime(task.created_at)}
                      </TableCell>
                      <TableCell className="text-xs text-stone-500">
                        {formatDateTime(task.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          {canCancel && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => void handleCancel(task.id)}
                              disabled={isProcessing}
                              className="border-red-200 text-red-600 hover:bg-red-50"
                            >
                              {isProcessing ? (
                                <LoaderCircle className="size-3 animate-spin" />
                              ) : (
                                <XCircle className="size-3" />
                              )}
                              取消
                            </Button>
                          )}
                          {canRetry && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => void handleRetry(task.id)}
                              disabled={isProcessing}
                            >
                              {isProcessing ? (
                                <LoaderCircle className="size-3 animate-spin" />
                              ) : (
                                <RotateCcw className="size-3" />
                              )}
                              重试
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

export default function ImageTasksPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  }
  return <ImageTaskManager />;
}

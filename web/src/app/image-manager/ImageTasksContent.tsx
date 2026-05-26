"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCw, XCircle, RotateCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { adminFetchImageTasks, adminCancelImageTask, adminRetryImageTask, type ImageTask } from "@/lib/api";

function StatusBadge({ status }: { status: ImageTask["status"] }) {
  if (status === "success") return <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 border-emerald-200">成功</Badge>;
  if (status === "error") return <Badge className="bg-rose-100 text-rose-700 hover:bg-rose-100 border-rose-200">失败</Badge>;
  if (status === "cancelled") return <Badge className="bg-stone-100 text-stone-700 hover:bg-stone-100 border-stone-200">已取消</Badge>;
  if (status === "running") return <Badge className="bg-blue-100 text-blue-700 hover:bg-blue-100 border-blue-200">运行中</Badge>;
  return <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100 border-amber-200">排队中</Badge>;
}

export function ImageTasksContent() {
  const [tasks, setTasks] = useState<ImageTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [actionTarget, setActionTarget] = useState<{ task: ImageTask, action: "cancel" | "retry" } | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  const loadTasks = async () => {
    setIsLoading(true);
    try {
      const data = await adminFetchImageTasks();
      setTasks(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载任务失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadTasks();
  }, []);

  const handleAction = async () => {
    if (!actionTarget) return;
    setIsProcessing(true);
    try {
      if (actionTarget.action === "cancel") {
        await adminCancelImageTask(actionTarget.task.id);
        toast.success("任务已取消");
      } else {
        await adminRetryImageTask(actionTarget.task.id);
        toast.success("任务已重试");
      }
      await loadTasks();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
    } finally {
      setIsProcessing(false);
      setActionTarget(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-stone-500">
          共 {tasks.length} 个任务
        </div>
        <Button variant="outline" className="h-8 rounded-lg px-3" onClick={() => void loadTasks()} disabled={isLoading}>
          <RefreshCw className={`mr-2 size-4 ${isLoading ? "animate-spin" : ""}`} />
          刷新
        </Button>
      </div>

      <Card className="overflow-hidden rounded-[26px] border-white/80 bg-white/86 shadow-[var(--shadow-soft)]">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-stone-600">
              <thead className="border-b border-stone-100 bg-stone-50/50 text-xs text-stone-500">
                <tr>
                  <th className="px-5 py-3 font-medium">任务 ID</th>
                  <th className="px-5 py-3 font-medium">状态</th>
                  <th className="px-5 py-3 font-medium">模型 / 模式</th>
                  <th className="px-5 py-3 font-medium">提示词</th>
                  <th className="px-5 py-3 font-medium">创建时间</th>
                  <th className="px-5 py-3 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {tasks.map((task) => (
                  <tr key={task.id} className="transition hover:bg-stone-50/50">
                    <td className="px-5 py-3 font-mono text-xs">{task.id.slice(0, 12)}...</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={task.status} />
                      {task.error && <div className="mt-1 text-[10px] text-rose-500 max-w-xs truncate" title={task.error}>{task.error}</div>}
                    </td>
                    <td className="px-5 py-3">
                      <div>{task.model || "-"}</div>
                      <div className="text-xs text-stone-400">{task.mode === "generate" ? "文生图" : "图生图"}</div>
                    </td>
                    <td className="px-5 py-3 max-w-[200px] truncate" title={task.prompt || "-"}>
                      {task.prompt || "-"}
                    </td>
                    <td className="px-5 py-3 text-xs text-stone-500">{task.created_at}</td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {(task.status === "queued" || task.status === "running") && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 text-stone-500 hover:text-stone-900"
                            onClick={() => setActionTarget({ task, action: "cancel" })}
                          >
                            <XCircle className="mr-1 size-4" /> 取消
                          </Button>
                        )}
                        {task.status === "error" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 text-stone-500 hover:text-stone-900"
                            onClick={() => setActionTarget({ task, action: "retry" })}
                          >
                            <RotateCw className="mr-1 size-4" /> 重试
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {tasks.length === 0 && !isLoading && (
                  <tr>
                    <td colSpan={6} className="px-5 py-12 text-center text-stone-500">
                      没有找到任务
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={!!actionTarget} onOpenChange={(open) => { if (!open) setActionTarget(null); }}>
        <DialogContent className="max-w-sm rounded-2xl">
          <DialogHeader>
            <DialogTitle>{actionTarget?.action === "cancel" ? "取消任务" : "重试任务"}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-stone-600">
            {actionTarget?.action === "cancel" 
              ? "确定要取消这个任务吗？取消后任务将停止执行。" 
              : "确定要重试这个失败的任务吗？将使用原始参数重新加入队列。"}
          </p>
          <DialogFooter>
            <Button variant="outline" className="rounded-xl" onClick={() => setActionTarget(null)} disabled={isProcessing}>
              返回
            </Button>
            <Button 
              variant={actionTarget?.action === "cancel" ? "destructive" : "default"} 
              className="rounded-xl" 
              onClick={handleAction} 
              disabled={isProcessing}
            >
              {isProcessing && <LoaderCircle className="mr-2 size-4 animate-spin" />}
              {actionTarget?.action === "cancel" ? "确认取消" : "确认重试"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
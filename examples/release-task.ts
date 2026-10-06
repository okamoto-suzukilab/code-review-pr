export function releaseTask(taskId: string) {
  if (taskId.trim().length === 0) {
    throw new Error("Task ID must not be empty");
  }

  return fetch(`/api/tasks/${encodeURIComponent(taskId)}/release`, {
    method: "POST",
    keepalive: true,
  });
}

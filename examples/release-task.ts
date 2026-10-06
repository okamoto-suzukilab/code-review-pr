export function releaseTask(taskId: string) {
  return fetch(`/api/tasks/${encodeURIComponent(taskId)}/release`, {
    method: "POST",
    keepalive: true,
  });
}

// GPU resource routes - the VRAM orchestrator's actions on a waiting lease, and
// the one that frees ComfyUI's cache. The state they act on is read from
// `GET /workers/progress` (`resources`), which `useTasksStore` polls.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Start a waiting lease now, past the memory it is short of.
 * 404 for an unknown lease; 409 when it is not waiting any more.
 *
 * @param {string} leaseId
 * @returns {Promise<{status: string, lease: Object}>}
 */
export async function runLeaseAnyway(leaseId) {
  return unwrap(
    apiClient.post(`/resources/leases/${encodeURIComponent(leaseId)}/run-anyway`),
  );
}

/**
 * Cancel a waiting lease, and the ComfyUI run held behind it.
 * 404 for an unknown lease; 409 when it is not waiting any more.
 *
 * @param {string} leaseId
 * @returns {Promise<{status: string}>}
 */
export async function cancelLease(leaseId) {
  return unwrap(
    apiClient.post(`/resources/leases/${encodeURIComponent(leaseId)}/cancel`),
  );
}

/**
 * Ask ComfyUI to free its cache. Refused (409) while ComfyUI's own queue is not
 * empty, and 503 when ComfyUI cannot be reached.
 *
 * @returns {Promise<{status: string, freed_mb: number}>}
 */
export async function freeComfyui() {
  return unwrap(apiClient.post("/resources/comfyui/free"));
}

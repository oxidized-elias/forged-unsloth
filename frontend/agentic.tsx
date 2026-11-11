import { useEffect, useState } from "react";
import { Box } from "lucide-react";
import { authFetch } from "@/features/auth/api";
import { useChatRuntimeStore } from "@/features/chat/stores/chat-runtime-store";
import {
	createMcpServer,
	listMcpServers,
	updateMcpServer,
} from "@/features/chat/api/mcp-servers-api";

type Workspace = {
	enabled: boolean;
	network: boolean;
	image: string;
	folders: string[];
};
async function request<T>(
	path: string,
	method = "GET",
	body?: unknown,
): Promise<T> {
	const response = await authFetch(`/api/forged${path}`, {
		method,
		headers: body ? { "Content-Type": "application/json" } : undefined,
		body: body ? JSON.stringify(body) : undefined,
	});
	const result = await response.json();
	if (!response.ok)
		throw new Error(
			typeof result.detail === "string"
				? result.detail
				: "Workspace request failed",
		);
	return result as T;
}

export function AgenticPill() {
	const mcpEnabled = useChatRuntimeStore((state) => state.mcpEnabledForChat);
	const setMcpEnabled = useChatRuntimeStore(
		(state) => state.setMcpEnabledForChat,
	);
	const setCodeEnabled = useChatRuntimeStore(
		(state) => state.setCodeToolsEnabled,
	);
	const [enabled, setEnabled] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	useEffect(() => {
		let live = true;
		const refresh = () => {
			void request<{ config: Workspace }>("/status")
				.then((status) => {
					if (live) setEnabled(status.config.enabled);
				})
				.catch(() => undefined);
		};
		refresh();
		window.addEventListener("forged-workspace", refresh);
		return () => {
			live = false;
			window.removeEventListener("forged-workspace", refresh);
		};
	}, []);
	const active = enabled && mcpEnabled;
	async function toggle() {
		setBusy(true);
		setError("");
		try {
			const status = await request<{
				config: Workspace;
				podman_available: boolean;
				mcp_command: string;
			}>("/status");
			if (!active && !status.podman_available)
				throw new Error(
					"Install rootless Podman, then configure folders in Forged Unsloth → Agent workspace.",
				);
			const next = !active;
			const existing = (await listMcpServers()).find(
				(server) => server.display_name === "Forged Unsloth Workspace",
			);
			if (existing) await updateMcpServer(existing.id, { isEnabled: next });
			else if (next)
				await createMcpServer({
					displayName: "Forged Unsloth Workspace",
					url: status.mcp_command,
					isEnabled: true,
				});
			await request("/workspace", "PUT", { ...status.config, enabled: next });
			if (next) {
				setMcpEnabled(true);
				setCodeEnabled(false);
			}
			setEnabled(next);
			window.dispatchEvent(new Event("forged-workspace"));
		} catch (failure) {
			setError(failure instanceof Error ? failure.message : String(failure));
		} finally {
			setBusy(false);
		}
	}
	return (
		<span className="relative inline-flex">
			<button
				type="button"
				className="composer-pill-btn"
				data-pill-label="Agentic"
				data-active={active ? "true" : "false"}
				aria-label={
					active ? "Disable Agentic workspace" : "Enable Agentic workspace"
				}
				aria-pressed={active}
				disabled={busy}
				title={
					error ||
					"Agentic: run commands in Podman and edit your connected folders. Configure folders in Forged Unsloth."
				}
				onClick={() => void toggle()}
			>
				<Box aria-hidden="true" className="size-4" />
				<span>Agentic</span>
			</button>
			{error && (
				<span
					role="alert"
					className="absolute bottom-full left-0 z-50 mb-2 w-72 rounded-lg border border-border bg-popover p-3 text-xs text-popover-foreground"
					onClick={() => setError("")}
				>
					{error}
				</span>
			)}
		</span>
	);
}

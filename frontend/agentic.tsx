import { useEffect, useState } from "react";
import { Box, Square } from "lucide-react";
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

export async function interruptAgenticWorkspace() {
	window.dispatchEvent(
		new CustomEvent("forged-interrupt-state", { detail: { pending: true } }),
	);
	try {
		const result = await request<{ interrupted: boolean }>(
			"/workspace/interrupt",
			"POST",
		);
		window.dispatchEvent(
			new CustomEvent("forged-interrupt-state", { detail: { pending: false } }),
		);
		return result;
	} catch (failure) {
		window.dispatchEvent(
			new CustomEvent("forged-interrupt-state", {
				detail: {
					pending: false,
					error: failure instanceof Error ? failure.message : String(failure),
				},
			}),
		);
		throw failure;
	}
}

export function AgenticPill({
	onStopGeneration,
}: { onStopGeneration?: () => void } = {}) {
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
	const [interrupting, setInterrupting] = useState(false);
	useEffect(() => {
		const update = (event: Event) => {
			const detail = (
				event as CustomEvent<{ pending: boolean; error?: string }>
			).detail;
			setInterrupting(detail.pending);
			setError(detail.error || "");
		};
		window.addEventListener("forged-interrupt-state", update);
		return () => window.removeEventListener("forged-interrupt-state", update);
	}, []);
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
			{enabled && (
				<button
					type="button"
					className="composer-pill-btn"
					data-pill-label="Interrupt"
					aria-label="Interrupt Agentic workspace"
					disabled={interrupting}
					title="Stop the AI and all running sandbox commands. Installed tools and files are kept."
					onClick={() => {
						if (onStopGeneration) onStopGeneration();
						else void interruptAgenticWorkspace().catch(() => undefined);
					}}
				>
					<Square aria-hidden="true" className="size-3.5" />
					<span>{interrupting ? "Interrupting…" : "Interrupt"}</span>
				</button>
			)}
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

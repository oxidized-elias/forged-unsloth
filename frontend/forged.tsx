import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { authFetch } from "@/features/auth";
import { apiUrl } from "@/lib/api-base";
import {
	storeAuthTokens,
	setMustChangePassword,
} from "@/features/auth/session";
import {
	listMcpServers,
	createMcpServer,
	importMcpServers,
	updateMcpServer,
	deleteMcpServer,
} from "@/features/chat/api/mcp-servers-api";
import { useMcpServersDialogStore } from "@/features/chat/stores/mcp-servers-dialog-store";
import {
	loadLanAccess,
	startLanAccess,
	stopLanAccess,
} from "@/features/settings/api/lan-access";
import {
	loadRemoteAccess,
	startRemoteAccess,
	stopRemoteAccess,
} from "@/features/settings/api/remote-access";
import "./forged.css";

type Workspace = {
	enabled: boolean;
	image: string;
	network: boolean;
	folders: string[];
};
type Status = {
	config: Workspace;
	podman_available: boolean;
	mcp_command: string;
};
const rows = {
	newChat: "New Chat",
	hub: "Model Hub",
	projects: "Projects",
	library: "Library",
	images: "Images",
	video: "Video",
	audio: "Audio",
	train: "Training",
	recipes: "Data Recipes",
	export: "Export",
	api: "API",
	tasks: "Automations",
};
const NAV_EVENT = "forged-navigation";
function preferences(): string[] {
	try {
		const value: unknown = JSON.parse(
			localStorage.getItem("forged-hidden-tabs") || "[]",
		);
		return Array.isArray(value)
			? value.filter((v): v is string => typeof v === "string" && v in rows)
			: [];
	} catch {
		return [];
	}
}
function applyNavigation() {
	const hidden = preferences();
	for (const id of Object.keys(rows))
		document.documentElement.toggleAttribute(
			`data-forged-hide-${id.toLowerCase()}`,
			hidden.includes(id),
		);
}
window.addEventListener(NAV_EVENT, applyNavigation);
window.addEventListener("storage", applyNavigation);
applyNavigation();

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
				: `Request failed (${response.status})`,
		);
	return result as T;
}

export function ForgedRemote() {
	return <ForgedPanel />;
}

function ForgedPanel() {
	const [status, setStatus] = useState<Status | null>(null);
	const [settings, setSettings] = useState<Workspace>({
		enabled: false,
		network: true,
		image: "docker.io/library/debian:13-slim",
		folders: [],
	});
	const [folders, setFolders] = useState("");
	const [key, setKey] = useState("");
	const [lan, setLan] = useState<Awaited<
		ReturnType<typeof loadLanAccess>
	> | null>(null);
	const [remote, setRemote] = useState<Awaited<
		ReturnType<typeof loadRemoteAccess>
	> | null>(null);
	const [showKey, setShowKey] = useState(false);
	const [message, setMessage] = useState("");
	const [busy, setBusy] = useState(false);
	const [mcpJson, setMcpJson] = useState('{"mcpServers": {}}');
	const [hidden, setHidden] = useState(preferences);
	const [servers, setServers] = useState<
		Awaited<ReturnType<typeof listMcpServers>>
	>([]);
	const run = async (action: () => Promise<void>) => {
		setBusy(true);
		setMessage("");
		try {
			await action();
		} catch (error) {
			setMessage(error instanceof Error ? error.message : String(error));
		} finally {
			setBusy(false);
		}
	};
	useEffect(() => {
		let live = true;
		void Promise.all([request<Status>("/status"), listMcpServers()])
			.then(([value, connections]) => {
				if (live) {
					setStatus(value);
					setSettings(value.config);
					setFolders(value.config.folders.join("\n"));
					setServers(connections);
				}
			})
			.catch((error) => {
				if (live) setMessage(String(error));
			});
		return () => {
			live = false;
		};
	}, []);
	return (
		<div className="forged-panel">
			<h1>Forged Unsloth</h1>
			<p className="forged-muted">Your models. Your workspace.</p>
			<p role="status" aria-live="polite">
				{message}
			</p>
			<fieldset disabled={busy}>
				<legend>Remote &amp; LAN</legend>
				<div className="forged-actions">
					<button
						onClick={() =>
							void run(async () => {
								setLan(await loadLanAccess());
								setRemote(await loadRemoteAccess());
							})
						}
					>
						Refresh access status
					</button>
					<button
						onClick={() =>
							void run(async () => {
								setLan(await startLanAccess());
								setMessage("LAN listener requested");
							})
						}
					>
						Enable LAN
					</button>
					<button
						onClick={() =>
							void run(async () => {
								setLan(await stopLanAccess());
							})
						}
					>
						Stop LAN
					</button>
					<button
						onClick={() =>
							void run(async () => {
								setRemote(await startRemoteAccess());
								setMessage(
									"Remote tunnel requested. Refresh status for the public URL.",
								);
							})
						}
					>
						Enable remote link
					</button>
					<button
						onClick={() =>
							void run(async () => {
								setRemote(await stopRemoteAccess());
							})
						}
					>
						Stop remote link
					</button>
				</div>
				{(lan || remote) && (
					<details open>
						<summary>Listener status and addresses</summary>
						<pre>{JSON.stringify({ lan, remote }, null, 2)}</pre>
					</details>
				)}
				<p>
					Use the startup API key for LAN clients and external agents. A new key
					is generated each server startup. Reroll revokes the previous key.
				</p>
				<label>
					API key
					<input
						type={showKey ? "text" : "password"}
						readOnly
						value={key}
						placeholder="Reveal your startup key"
					/>
				</label>
				<div className="forged-actions">
					<button
						onClick={() =>
							void run(async () => {
								setKey((await request<{ api_key: string }>("/key")).api_key);
								setShowKey(true);
							})
						}
					>
						Reveal key
					</button>
					<button onClick={() => setShowKey(false)}>Hide</button>
					<button
						onClick={() =>
							void run(async () => {
								setKey(
									(await request<{ api_key: string }>("/key/rotate", "POST"))
										.api_key,
								);
								setShowKey(true);
								setMessage("Previous key revoked. Update your clients.");
							})
						}
					>
						Reroll key
					</button>
					<button
						disabled={!key}
						onClick={() =>
							void run(async () => {
								await navigator.clipboard.writeText(key);
								setMessage("Key copied");
							})
						}
					>
						Copy key
					</button>
				</div>
				<label>
					OpenAI-compatible base URL
					<input
						readOnly
						value={new URL(apiUrl("/v1"), window.location.href).href}
					/>
				</label>
				<p>
					Bind the API service to your LAN address to accept LAN connections.
					External agents use their own filesystem and command tools; no Podman
					integration is added to their requests.
				</p>
				<details>
					<summary>External client setup</summary>
					<pre>
						{JSON.stringify(
							{
								provider: {
									unsloth: {
										npm: "@ai-sdk/openai-compatible",
										name: "Forged Unsloth",
										options: {
											baseURL: new URL(apiUrl("/v1"), window.location.href)
												.href,
											apiKey: "YOUR_STARTUP_KEY",
										},
										models: { YOUR_LOADED_MODEL_ID: { name: "Local model" } },
									},
								},
							},
							null,
							2,
						)}
					</pre>
					<pre>
						{JSON.stringify(
							{
								mcpServers: {
									"forged-unsloth": {
										type: "http",
										url: new URL(apiUrl("/mcp"), window.location.href).href,
										headers: { Authorization: "Bearer YOUR_STARTUP_KEY" },
									},
								},
							},
							null,
							2,
						)}
					</pre>
					<p>
						Claude Code: add this entry to .mcp.json after enabling Studio MCP
						in Settings → API.
					</p>
					<p>
						OpenCode: use this provider in opencode.json. Other
						OpenAI-compatible clients use the same base URL and Bearer key.
						Claude Code can access Studio tools through the existing /mcp
						endpoint after enabling Studio MCP in Settings → API; this does not
						make /v1 an Anthropic Messages API.
					</p>
				</details>
			</fieldset>
			<fieldset disabled={busy}>
				<legend>Integrations</legend>
				<p>
					Connect MCP tools using LM Studio / Cursor style mcp.json. Studio
					handles tool discovery, OAuth, and per-chat selection.
				</p>
				<label>
					MCP configuration
					<textarea
						rows={5}
						value={mcpJson}
						onChange={(event) => setMcpJson(event.target.value)}
						spellCheck={false}
					/>
				</label>
				<button
					onClick={() =>
						void run(async () => {
							const result = await importMcpServers(JSON.parse(mcpJson));
							setServers(await listMcpServers());
							setMessage(
								`Imported ${result.created.length}; skipped ${result.skipped.length}. ${result.errors.join("; ")}`,
							);
						})
					}
				>
					Import integrations
				</button>
				<button
					onClick={() => useMcpServersDialogStore.getState().setOpen(true)}
				>
					Manage integrations
				</button>
				{servers.map((server) => (
					<div className="forged-connection" key={server.id}>
						<label>
							<input
								type="checkbox"
								checked={server.is_enabled}
								onChange={(event) => {
									const enabled = event.target.checked;
									void run(async () => {
										await updateMcpServer(server.id, { isEnabled: enabled });
										setServers(await listMcpServers());
									});
								}}
							/>
							{server.display_name}
						</label>
						<button
							onClick={() =>
								void run(async () => {
									await deleteMcpServer(server.id);
									setServers(await listMcpServers());
								})
							}
						>
							Remove
						</button>
					</div>
				))}
			</fieldset>
			<fieldset disabled={busy || !status}>
				<legend>Agent workspace</legend>
				<p>
					{status?.podman_available
						? "Podman found"
						: "Install rootless Podman to run workspace tools"}
					. Connected folders are writable: changes affect your files directly.
				</p>
				<label>
					<input
						type="checkbox"
						checked={settings.enabled}
						onChange={(event) =>
							setSettings({ ...settings, enabled: event.target.checked })
						}
					/>
					Enable Podman tools for GUI chat
				</label>
				<label>
					Container image
					<input
						value={settings.image}
						onChange={(event) =>
							setSettings({ ...settings, image: event.target.value })
						}
					/>
				</label>
				<p>
					The default is a persistent Debian sandbox. The model can use apt-get,
					download installers, and run any language or application inside it.
					Installed tools remain available between commands.
				</p>
				<label>
					Connected folders — one absolute path per line
					<textarea
						rows={4}
						value={folders}
						onChange={(event) => setFolders(event.target.value)}
						placeholder="/home/you/projects/my-app"
					/>
				</label>
				<p>
					Mounted at /projects/0, /projects/1, … . Files, installed packages,
					and /root persist. Changing the image, folders, or network policy
					selects a different sandbox; the previous one is kept stopped.
				</p>
				<label>
					<input
						type="checkbox"
						checked={settings.network}
						onChange={(event) =>
							setSettings({ ...settings, network: event.target.checked })
						}
					/>
					Allow downloads and network access (on by default)
				</label>
				<button
					onClick={() =>
						void run(async () => {
							const value = {
								...settings,
								folders: folders
									.split("\n")
									.map((path) => path.trim())
									.filter(Boolean),
							};
							const saved = await request<Workspace>(
								"/workspace",
								"PUT",
								value,
							);
							setSettings(saved);
							window.dispatchEvent(new Event("forged-workspace"));
							const existing = (await listMcpServers()).find(
								(server) => server.display_name === "Forged Unsloth Workspace",
							);
							if (existing)
								await updateMcpServer(existing.id, {
									isEnabled: saved.enabled,
								});
							else if (saved.enabled && status)
								await createMcpServer({
									displayName: "Forged Unsloth Workspace",
									url: status.mcp_command,
									isEnabled: true,
								});
							setServers(await listMcpServers());
							setMessage(
								"Workspace saved. Select Forged Unsloth Workspace in the chat tool picker.",
							);
						})
					}
				>
					Save workspace
				</button>
				<button
					onClick={() =>
						void run(async () => {
							await request("/workspace/interrupt", "POST");
							setMessage(
								"Sandbox interrupted. Installed tools and files are preserved; the next command can restart it.",
							);
						})
					}
				>
					Interrupt sandbox
				</button>
				<p>
					Containers are tools, available alongside Studio’s existing Skills.
					With the workspace enabled, GUI terminal, Python, and edit-file calls
					must use these container tools.
				</p>
			</fieldset>
			<fieldset>
				<legend>Automations</legend>
				<p>
					Run a predefined prompt with a chosen model at a specific time. Create
					one-time, hourly, daily, weekly, or monthly automations with
					timezone-aware schedules, notifications, pause/resume, and run
					history.
				</p>
				<button onClick={() => window.location.assign("/tasks")}>
					Open Automations
				</button>
				<p>
					Studio must stay running when an automation is due. Results appear in
					a new chat.
				</p>
			</fieldset>
			<fieldset>
				<legend>Visible navigation</legend>
				<p>
					Hide any main navigation entry. Settings and the Forged Unsloth button
					remain available to restore entries.
				</p>
				<div className="forged-toggles">
					{Object.entries(rows).map(([id, label]) => (
						<label key={id}>
							<input
								type="checkbox"
								checked={!hidden.includes(id)}
								onChange={(event) => {
									const next = event.target.checked
										? hidden.filter((value) => value !== id)
										: [...hidden, id];
									setHidden(next);
									localStorage.setItem(
										"forged-hidden-tabs",
										JSON.stringify(next),
									);
									window.dispatchEvent(new Event(NAV_EVENT));
								}}
							/>
							{label}
						</label>
					))}
				</div>
			</fieldset>
		</div>
	);
}

function Launcher() {
	const [open, setOpen] = useState(false);
	const [loginKey, setLoginKey] = useState("");
	const [error, setError] = useState("");
	return (
		<>
			<button className="forged-launcher" onClick={() => setOpen(true)}>
				Forged Unsloth
			</button>
			{open && (
				<dialog open className="forged-dialog" aria-label="Forged Unsloth">
					<button
						className="forged-close"
						onClick={() => setOpen(false)}
						aria-label="Close Forged Unsloth"
					>
						Close
					</button>
					{localStorage.getItem("unsloth_auth_token") ? (
						<ForgedPanel />
					) : (
						<div className="forged-panel">
							<h1>Connect with API key</h1>
							<p>Use the startup key from the owner's Studio installation.</p>
							<form
								onSubmit={(event) => {
									event.preventDefault();
									void (async () => {
										try {
											const response = await fetch(
												apiUrl("/api/forged/connect"),
												{ headers: { Authorization: `Bearer ${loginKey}` } },
											);
											if (!response.ok) throw new Error("Key rejected");
											storeAuthTokens(loginKey, "");
											setMustChangePassword(false);
											window.location.assign("/chat");
										} catch (err) {
											setError(String(err));
										}
									})();
								}}
							>
								<label>
									API key
									<input
										type="password"
										autoComplete="off"
										value={loginKey}
										onChange={(event) => setLoginKey(event.target.value)}
									/>
								</label>
								<button type="submit">Connect</button>
								<p role="alert">{error}</p>
							</form>
						</div>
					)}
				</dialog>
			)}
		</>
	);
}
const container = document.createElement("div");
container.id = "forged-unsloth";
document.body.append(container);
createRoot(container).render(<Launcher />);

export function ForgedAbout() {
	return (
		<section
			aria-labelledby="forged-about-title"
			className="rounded-xl border border-border p-4 space-y-3"
			data-testid="forged-about"
		>
			<div className="flex items-center gap-2">
				<h2 id="forged-about-title" className="font-semibold">
					Forged Unsloth
				</h2>
				<span className="forged-brand-badge rounded-full px-2 py-0.5 text-xs font-medium">
					Forged
				</span>
			</div>
			<p className="text-sm text-muted-foreground">
				Version 0.1.0 · Local injection plugin for Unsloth Studio
			</p>
			<dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-xs">
				<dt>Appearance</dt>
				<dd>Claude-inspired charcoal, warm orange #D97757, and off-white.</dd>
				<dt>Integrations</dt>
				<dd>
					MCP connections and mcp.json import, using Studio’s native
					integrations and Skills.
				</dd>
				<dt>Remote &amp; LAN</dt>
				<dd>
					Startup API keys with manual rotation; direct OpenAI-compatible API
					and Studio MCP access.
				</dd>
				<dt>GUI agent workspace</dt>
				<dd>
					Rootless Podman tools with user-selected writable folders and optional
					networking.
				</dd>
				<dt>Automations</dt>
				<dd>
					Scheduled prompts with a chosen model, timezone-aware repeat
					schedules, notifications, and run history.
				</dd>
				<dt>Navigation</dt>
				<dd>Individual visibility switches for all main sidebar tabs.</dd>
				<dt>Installation</dt>
				<dd>
					Injected into this Studio installation. Persists across restarts;
					verify and reinstall after application updates.
				</dd>
			</dl>
			<p className="text-xs text-muted-foreground">
				Independent customization. Unsloth Studio’s original version, credits,
				and license information remain below.
			</p>
		</section>
	);
}

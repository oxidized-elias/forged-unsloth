import { useSyncExternalStore } from "react";
const key = "forged-hidden-tabs";
function snapshot() {
	try {
		return localStorage.getItem(key) || "[]";
	} catch {
		return "[]";
	}
}
function subscribe(notify: () => void) {
	window.addEventListener("forged-navigation", notify);
	window.addEventListener("storage", notify);
	return () => {
		window.removeEventListener("forged-navigation", notify);
		window.removeEventListener("storage", notify);
	};
}
export function useForgedHidden(): string[] {
	const value = useSyncExternalStore(subscribe, snapshot, () => "[]");
	try {
		const ids: unknown = JSON.parse(value);
		return Array.isArray(ids)
			? ids.filter((id): id is string => typeof id === "string")
			: [];
	} catch {
		return [];
	}
}

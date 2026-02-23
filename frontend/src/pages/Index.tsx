import { useState } from "react";
import { useHealthCheck } from "@/hooks/useHealthCheck";
import { ChatTab } from "@/components/ChatTab";
import { DataTab } from "@/components/DataTab";
import { SettingsTab } from "@/components/SettingsTab";
import {
  MessageSquare,
  Database,
  Settings,
  Brain,
  CheckCircle2,
  X,
  Loader2,
} from "lucide-react";

type Tab = "chat" | "data" | "settings";

export default function Index() {
  const [activeTab, setActiveTab] = useState<Tab>("chat");
  const { status } = useHealthCheck();

  const tabs = [
    { id: "chat" as Tab, label: "Chat", icon: MessageSquare },
    { id: "data" as Tab, label: "Data", icon: Database },
    { id: "settings" as Tab, label: "Settings", icon: Settings },
  ];

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="flex-shrink-0 border-b border-border px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center glow-sm">
            <Brain className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-foreground tracking-tight">
              LifeQuery
            </h1>
            <p className="text-xs text-muted-foreground font-mono">
              local memory engine
            </p>
          </div>
        </div>

        <nav className="flex gap-1 bg-secondary rounded-lg p-1">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  activeTab === tab.id
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="flex items-center gap-2">
          {status === "connected" ? (
            <>
              <CheckCircle2 className="w-4 h-4 text-success" />
              <span className="text-xs font-mono text-success">Connected</span>
            </>
          ) : status === "disconnected" ? (
            <>
              <X className="w-4 h-4 text-destructive" />
              <span className="text-xs font-mono text-destructive">
                Disconnected
              </span>
            </>
          ) : (
            <>
              <Loader2 className="w-4 h-4 text-warning animate-spin" />
              <span className="text-xs font-mono text-warning">
                Connecting...
              </span>
            </>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-hidden">
        <div className={`h-full ${activeTab === "chat" ? "" : "hidden"}`}>
          <ChatTab />
        </div>
        <div
          className={`h-full overflow-y-auto ${
            activeTab === "data" ? "" : "hidden"
          }`}
        >
          <DataTab />
        </div>
        <div
          className={`h-full overflow-y-auto ${
            activeTab === "settings" ? "" : "hidden"
          }`}
        >
          <SettingsTab />
        </div>
      </main>
    </div>
  );
}

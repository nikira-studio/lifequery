import React from "react";
import { HelpCircle } from "lucide-react";

export function SettingField({
  label,
  children,
  helpText,
}: {
  label: string;
  children: React.ReactNode;
  helpText?: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <label className="text-xs font-medium text-muted-foreground">
          {label}
        </label>
        {helpText && (
          <div className="group relative">
            <HelpCircle className="w-3 h-3 text-muted-foreground/40 hover:text-primary transition-colors cursor-help" />
            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 w-48 p-2 bg-popover border border-border rounded shadow-xl text-[9px] text-foreground font-medium hidden group-hover:block z-50 animate-in fade-in zoom-in duration-200">
              {helpText}
              <div className="absolute top-full left-1/2 -translate-x-1/2 border-8 border-transparent border-t-popover" />
            </div>
          </div>
        )}
      </div>
      {children}
    </div>
  );
}

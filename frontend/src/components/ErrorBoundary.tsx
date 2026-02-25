
import { Component, ErrorInfo, ReactNode } from "react";
import { AlertCircle, RefreshCcw } from "lucide-react";

interface Props {
    children?: ReactNode;
}

interface State {
    hasError: boolean;
    error?: Error;
}

class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error("Uncaught error:", error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div className="flex flex-col items-center justify-center p-8 bg-card border border-destructive/20 rounded-xl space-y-4 text-center my-4 animate-in fade-in zoom-in-95 duration-300">
                    <div className="w-16 h-16 rounded-full bg-destructive/10 flex items-center justify-center">
                        <AlertCircle className="w-8 h-8 text-destructive" />
                    </div>
                    <div className="space-y-2">
                        <h3 className="text-xl font-bold text-foreground">Something went wrong</h3>
                        <p className="text-sm text-muted-foreground max-w-xs">
                            The component crashed while rendering. This is usually caused by unexpected data from the server.
                        </p>
                    </div>

                    {this.state.error && (
                        <div className="bg-muted p-3 rounded-md text-[10px] font-mono text-muted-foreground max-w-md overflow-auto border border-border">
                            {this.state.error.message}
                        </div>
                    )}

                    <button
                        onClick={() => window.location.reload()}
                        className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 transition-all shadow-lg shadow-primary/20"
                    >
                        <RefreshCcw className="w-4 h-4" />
                        Reload Page
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;

import { AlertCircle, Key } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface ApiKeyErrorDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    error: string | null;
    errorCode: string | null;
    onNavigateToDevelopers: () => void;
    onNavigateToModelConfig: () => void;
}

export const ApiKeyErrorDialog = ({
    open,
    onOpenChange,
    error,
    errorCode,
    onNavigateToDevelopers,
    onNavigateToModelConfig,
}: ApiKeyErrorDialogProps) => {
    const isServiceKeyError = errorCode === 'invalid_service_key';

    const title = "API Configuration Error";
    const icon = <Key className="h-5 w-5 text-red-500" />;
    const buttonText = isServiceKeyError
        ? "Go to Developers"
        : "Go to Model Configurations";
    const onNavigate = isServiceKeyError
        ? onNavigateToDevelopers
        : onNavigateToModelConfig;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        {icon}
                        {title}
                    </DialogTitle>
                    <DialogDescription className="pt-3" asChild>
                        <div className="flex items-start gap-2">
                            <AlertCircle className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                            <div className="text-sm space-y-1">
                                <p className="font-medium text-foreground">{error}</p>
                            </div>
                        </div>
                    </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={onNavigate}>
                        {buttonText}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

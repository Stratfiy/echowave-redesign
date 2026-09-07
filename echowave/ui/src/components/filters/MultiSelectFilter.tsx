import { ChevronDown, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { MultiSelectValue } from "@/types/filters";

interface MultiSelectFilterProps {
  options: string[];
  value: MultiSelectValue;
  onChange: (value: MultiSelectValue) => void;
  error?: string;
  showSelectAll?: boolean;
  searchable?: boolean;
  /**
   * Offer "any"/"all". Only for a field a row can hold several of — asking it
   * about a single-valued field would be offering a choice with one real
   * answer.
   */
  matchModes?: boolean;
  optionsLoading?: boolean;
}

export const MultiSelectFilter: React.FC<MultiSelectFilterProps> = ({
  options,
  value,
  onChange,
  error,
  showSelectAll = true,
  searchable = true,
  matchModes = false,
  optionsLoading = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");

  const filteredOptions = searchable
    ? options.filter(option =>
        option.toLowerCase().includes(searchTerm.toLowerCase())
      )
    : options;

  // The match survives every other edit: somebody who picked "all" and then
  // adds a third box has not changed their mind about how the boxes combine.
  const match = value.match ?? "any";

  const handleSelectAll = () => {
    onChange({ codes: options, match });
  };

  const handleSelectNone = () => {
    onChange({ codes: [], match });
  };

  const handleToggleOption = (option: string) => {
    const newCodes = value.codes.includes(option)
      ? value.codes.filter(code => code !== option)
      : [...value.codes, option];
    onChange({ codes: newCodes, match });
  };

  const getDisplayText = () => {
    if (value.codes.length === 0) return "Select options";
    if (value.codes.length <= 3) return value.codes.join(", ");
    return `${value.codes.slice(0, 3).join(", ")} +${value.codes.length - 3} more`;
  };

  return (
    <div className="space-y-2">
      <Label>Select Options</Label>
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={isOpen}
            className={cn(
              "w-full justify-between",
              value.codes.length === 0 && "text-muted-foreground"
            )}
          >
            <span className="truncate">{getDisplayText()}</span>
            <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-full p-0">
          <div className="p-2 space-y-2">
            {searchable && (
              <div className="relative">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search options..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-8"
                />
              </div>
            )}

            {showSelectAll && (
              <div className="flex gap-2 pb-2 border-b">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleSelectAll}
                  className="flex-1"
                >
                  Select All
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleSelectNone}
                  className="flex-1"
                >
                  Select None
                </Button>
              </div>
            )}

            <div className="max-h-[200px] overflow-auto space-y-1">
              {optionsLoading ? (
                <p className="text-sm text-muted-foreground text-center py-2">
                  Loading...
                </p>
              ) : filteredOptions.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-2">
                  No options found
                </p>
              ) : (
                filteredOptions.map((option) => (
                  <div
                    key={option}
                    className="flex items-center space-x-2 p-2 hover:bg-accent rounded-sm cursor-pointer"
                    onClick={() => handleToggleOption(option)}
                  >
                    <Checkbox
                      checked={value.codes.includes(option)}
                      onCheckedChange={() => handleToggleOption(option)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <Label
                      htmlFor={option}
                      className="text-sm font-normal cursor-pointer flex-1"
                    >
                      {option}
                    </Label>
                  </div>
                ))
              )}
            </div>

            <div className="pt-2 border-t space-y-2">
              {/* Only once there is something to combine. With one box ticked
                  the two options mean the same thing, and offering the choice
                  there just invites somebody to wonder which they wanted. */}
              {matchModes && value.codes.length > 1 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Match</span>
                  {(["any", "all"] as const).map((mode) => (
                    <Button
                      key={mode}
                      type="button"
                      size="sm"
                      variant={match === mode ? "default" : "outline"}
                      className="h-6 px-2 text-xs"
                      onClick={() => onChange({ codes: value.codes, match: mode })}
                    >
                      {mode === "any" ? "Any of these" : "All of these"}
                    </Button>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                {value.codes.length} selected
              </p>
            </div>
          </div>
        </PopoverContent>
      </Popover>

      {error && (
        <p className="text-sm text-red-500 mt-1">{error}</p>
      )}
    </div>
  );
};

/**
 * /tasks: with `today_list` off it is exactly the board it was; on, it is
 * Today (screen 07). Turning the flag off restores today's behaviour.
 */
import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const flags: Record<string, boolean> = {};
const listTasks = vi.fn();

vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ listTasksApiV1TasksGet: (...a: unknown[]) => listTasks(...a) }));
vi.mock("@/components/desk/SimpleTaskBoard", () => ({ SimpleTaskBoard: () => <div data-testid="simple-board" /> }));
vi.mock("@/components/desk/TaskBoard", () => ({ TaskBoard: () => <div data-testid="task-board" /> }));
vi.mock("@/components/today/TodayPage", () => ({ TodayPage: () => <div data-testid="today-page" /> }));

import TasksPage from "../page";

describe("/tasks", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        for (const key of Object.keys(flags)) delete flags[key];
        listTasks.mockResolvedValue({ data: { board: { enabled: false } } });
    });

    it("is the board while today_list is off", async () => {
        render(<TasksPage />);
        expect(await screen.findByTestId("simple-board")).toBeTruthy();
        expect(screen.queryByTestId("today-page")).toBeNull();
    });

    it("is Today while today_list is on, and does not load the board", async () => {
        flags.today_list = true;
        render(<TasksPage />);
        expect(await screen.findByTestId("today-page")).toBeTruthy();
        expect(listTasks).not.toHaveBeenCalled();
    });
});

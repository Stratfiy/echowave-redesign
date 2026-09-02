"use client";

/**
 * The first screen after signing in, and the only one whose job is to get
 * somebody to a working agent rather than to show them something.
 *
 * It used to open with "Open source alternative to Vapi — help us support the
 * project by giving us a star on GitHub", gated on `provider !== 'stack'`.
 * That condition is true for every local-auth deployment, which is what
 * production runs — so a paying customer's first impression was a request for
 * a GitHub star on a repository they have no relationship with, above a
 * comparison to a competitor. Both are gone. A customer is not a contributor.
 *
 * What replaced them is the order the work actually happens in: describe the
 * business, hear it on a call, put it on a number. The chat panel stays at the
 * top because it is the shortest path to a working agent, and the cards below
 * it are the next two steps rather than a directory of subsystems.
 */

import { ArrowRight, BookOpen, LifeBuoy, Phone, Sparkles } from 'lucide-react';
import Link from 'next/link';

import { AgentBuilderPanel } from '@/components/agent-builder/AgentBuilderPanel';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';

export default function OverviewPage() {
    const { user } = useAuth();
    const firstName = user?.displayName?.split(' ')[0];

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mx-auto max-w-4xl">
                <div className="mb-8">
                    <h1 className="text-3xl font-medium tracking-tight">
                        {firstName ? `Welcome, ${firstName}` : 'Welcome to Decibyl'}
                    </h1>
                    <p className="mt-2 text-muted-foreground">
                        Describe your business below and you will have an agent you
                        can talk to in a couple of minutes.
                    </p>
                </div>

                {/* The shortest path to a working agent. Everything else on this
                    page is what to do once it exists. */}
                <AgentBuilderPanel />

                {/* The next two steps, in the order they happen — not a list of
                    subsystems. Nothing here names a model vendor or a carrier:
                    the buyer this screen is for does not know one from another
                    and should not have to. */}
                <div className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-2">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg">
                                <Sparkles className="h-4 w-4 text-primary" />
                                Build it yourself
                            </CardTitle>
                            <CardDescription>
                                Prefer to start from a blank agent, or edit one you
                                have already made? The builder has every step of the
                                conversation.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Button asChild variant="outline">
                                <Link href="/workflow">
                                    Open agents
                                    <ArrowRight className="ml-2 h-4 w-4" />
                                </Link>
                            </Button>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-lg">
                                <Phone className="h-4 w-4 text-primary" />
                                Put it on a phone number
                            </CardTitle>
                            <CardDescription>
                                Once your agent sounds right, give it a number so
                                customers can call it — or connect a number you
                                already own.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Button asChild variant="outline">
                                <Link href="/telephony-configurations">
                                    Set up calling
                                    <ArrowRight className="ml-2 h-4 w-4" />
                                </Link>
                            </Button>
                        </CardContent>
                    </Card>
                </div>

                {/* Docs and a way to reach a person. The "Report an Issue" button
                    that stood here pointed at a GitHub issue tracker — the wrong
                    place to send a customer, who wants an answer rather than a
                    ticket in a queue they cannot see. */}
                <div className="mt-8 flex flex-wrap items-center gap-3">
                    <Button asChild variant="ghost" size="sm">
                        <a
                            href="https://docs.decibyl.ai"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            <BookOpen className="mr-2 h-4 w-4" />
                            Documentation
                        </a>
                    </Button>
                    <Button asChild variant="ghost" size="sm">
                        <a href="mailto:support@decibyl.ai">
                            <LifeBuoy className="mr-2 h-4 w-4" />
                            Get help
                        </a>
                    </Button>
                </div>
            </div>
        </div>
    );
}

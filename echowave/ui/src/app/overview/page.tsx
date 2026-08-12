"use client";

import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';

export default function OverviewPage() {
    const { user } = useAuth();

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="max-w-4xl mx-auto">
                {/* Welcome Card. Display type is light 300 with negative
                    tracking — the treatment Poppins was brought in for. */}
                <Card className="mb-8">
                    <CardHeader>
                        <CardTitle className="text-4xl font-light tracking-[-0.02em]">
                            {`Welcome${user?.displayName ? `, ${user.displayName.split(' ')[0]}` : ''}`}
                        </CardTitle>
                        <CardDescription className="text-lg mt-2">
                            Design a conversation, connect your providers, and start calling.
                        </CardDescription>
                    </CardHeader>
                </Card>

                {/* Quick Actions. These are the feature cards the pastel tints
                    exist for: the tint is the card's surface and nothing else —
                    the text, the border and the buttons on top are unchanged,
                    which is what keeps a tinted card readable and keeps the
                    warmth from turning into another accent. */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <Card className="bg-tint-peach">
                        <CardHeader>
                            <CardTitle>Create and Manage your Voice Agents</CardTitle>
                            <CardDescription>
                                Build powerful AI Voice Agents with our visual editor
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Button asChild>
                                <Link href="/workflow">
                                    Go to Agents
                                </Link>
                            </Button>
                        </CardContent>
                    </Card>

                    <Card className="bg-tint-sky">
                        <CardHeader>
                            <CardTitle>Configure Services</CardTitle>
                            <CardDescription>
                                Set up your AI services like LLM, TTS, and STT providers
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Button asChild variant="outline">
                                <Link href="/model-configurations">
                                    Configure Models
                                </Link>
                            </Button>
                        </CardContent>
                    </Card>
                </div>

                {/* Resources Section */}
                <Card className="mt-8 bg-tint-sand">
                    <CardHeader>
                        <CardTitle>Resources</CardTitle>
                        <CardDescription>
                            Get help and learn more about Decibyl
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap gap-4">
                            <Button asChild variant="outline">
                                <a
                                    href="https://docs.decibyl.ai"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                >
                                    Documentation
                                </a>
                            </Button>
                            <Button asChild variant="outline">
                                <a
                                    href="https://github.com/decibyl-hq/decibyl/issues"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                >
                                    Report an Issue
                                </a>
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

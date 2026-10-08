/** The app's one settler, over POST /timeline/actions/settle. */
import { settleActionApiV1TimelineActionsSettlePost } from '@/client/sdk.gen';
import { call } from '@/lib/api';

import { createSettler } from './confirmOnce';

export const settler = createSettler((request) =>
    call(settleActionApiV1TimelineActionsSettlePost({ body: request })),
);

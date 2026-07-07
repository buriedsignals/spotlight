import { sqlite } from '@flue/runtime/node';

// Durability (U8): persist the canonical conversation stream so a run interrupted on a slow
// local model resumes from durable deltas (completed output + tool results) rather than
// restarting. Node target only. Discovered at build time and wired into the server.
export default sqlite('./data/flue.db');

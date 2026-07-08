import { sqlite } from '@flue/runtime/node';

// Durability (U8): persist the canonical conversation stream so a run interrupted on a slow
// local model resumes from durable deltas (completed output + tool results) rather than
// restarting. Node target only. Discovered at build time and wired into the server.
// FLUE_DB gives each concurrent sampling run its own sqlite file (parallel gold-gen) so
// they don't contend/corrupt on one shared db; default = the single local db.
export default sqlite(process.env.FLUE_DB ?? './data/flue.db');

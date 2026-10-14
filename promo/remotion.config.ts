import { Config } from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
Config.setChromiumOpenGlRenderer('angle');
// Kept moderate: this machine also runs the Docker stack and has hit memory pressure.
Config.setConcurrency(4);

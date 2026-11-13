import { Snapshot } from './model';

export interface IEndpointIqProps {
  loadSnapshot: () => Promise<Snapshot>;
  colorScheme?: 'teal' | 'blue';
}

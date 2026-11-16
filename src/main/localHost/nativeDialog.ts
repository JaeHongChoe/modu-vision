import type {App, Dialog, OpenDialogOptions, OpenDialogReturnValue} from 'electron';

/** Called only after the service acquires its chooser lane. Bring this
 * windowless helper forward before opening its own native panel. */
export async function chooseNativeDialog(app: Pick<App, 'focus'>, dialog: Pick<Dialog, 'showOpenDialog'>,
 options: OpenDialogOptions): Promise<OpenDialogReturnValue> {
 app.focus({steal: true});
 return dialog.showOpenDialog(options);
}

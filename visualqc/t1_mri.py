"""

Module to present a base neuroimaging scan, currently T1 mri, without any overlay.

"""

import argparse
import sys
import textwrap
import warnings
from os import makedirs
from os.path import join as pjoin, exists as pexists, realpath
from shutil import copyfile

import matplotlib
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.widgets import CheckButtons
from mrivis.utils import crop_image

from visualqc import config as cfg
from visualqc.interfaces import BaseReviewInterface
from visualqc.utils import check_id_list, check_input_dir_T1, check_views, \
    check_finite_int, check_out_dir, check_outlier_params, get_path_for_subject, \
    read_image, scale_0to1, pick_slices, get_axis, get_ratings_path_info
from visualqc.workflows import BaseWorkflow

# each rating is a set of labels, join them with a plus delimiter
_plus_join = lambda label_set: '+'.join(label_set)

class T1MriInterface(BaseReviewInterface):
    """Custom interface for rating the quality of T1 MRI scan."""


    def __init__(self,
                 fig,
                 axes,
                 issue_list=cfg.t1_mri_default_issue_list,
                 next_button_callback=None,
                 quit_button_callback=None):
        """Constructor"""

        super().__init__(fig, axes, next_button_callback, quit_button_callback)

        self.issue_list = issue_list
        self.user_rated_issues = set()

        self.prev_axis = None
        self.prev_ax_pos = None
        self.zoomed_in = False
        self.add_checkboxes()

        self.next_button_callback = next_button_callback
        self.quit_button_callback = quit_button_callback

        # this list of artists to be populated later
        # makes to handy to clean them all
        self.data_handles = list()


    def add_checkboxes(self):
        """
        Checkboxes offer the ability to select multiple tags such as Motion, Ghosting Aliasing etc,
            instead of one from a list of mutual exclusive rating options (such as Good, Bad, Error etc).

        """

        ax_checkbox = plt.axes(cfg.position_checkbox, facecolor=cfg.color_rating_axis)
        # initially de-activating all
        actives = [False] * len(self.issue_list)
        self.checkbox = CheckButtons(ax_checkbox, labels=self.issue_list, actives=actives)
        self.checkbox.on_clicked(self.save_issues)
        for txt_lbl in self.checkbox.labels:
            txt_lbl.set(color=cfg.text_option_color, fontweight='normal')

        for rect in self.checkbox.rectangles:
            rect.set_width(cfg.checkbox_rect_width)
            rect.set_height(cfg.checkbox_rect_height)

        # lines is a list of n crosses, each cross (x) defined by a tuple of lines
        for x_line1, x_line2 in self.checkbox.lines:
            x_line1.set_color(cfg.checkbox_cross_color)
            x_line2.set_color(cfg.checkbox_cross_color)

        self._index_pass = cfg.t1_mri_default_issue_list.index(cfg.t1_mri_pass_indicator)

    def save_issues(self, label):
        """
        Update the rating

        This function is called whenever set_active() happens on any label, if checkbox.eventson is True.

        """

        print('Prev set : {}\nCurrent Label : {}'.format(self.get_ratings(), label))

        if label == cfg.t1_mri_pass_indicator:
            self.clear_checkboxes(except_pass=True)
        else:
            self.clear_pass_only_if_on()

        print('New set: {}'.format(self.get_ratings()))

    def clear_checkboxes(self, except_pass=False):
        """Clears all checkboxes.

        if except_pass=True,
            does not clear checkbox corresponding to cfg.t1_mri_pass_indicator
        """

        cbox_statuses = self.checkbox.get_status()
        for index, this_cbox_active in enumerate(cbox_statuses):
            if except_pass and index==self._index_pass:
                continue
            # if it was selected already, toggle it.
            if this_cbox_active:
                # not calling checkbox.set_active() as it calls the callback self.save_issues() each time, if eventson is True
                self._toggle_visibility_checkbox(index)

    def clear_pass_only_if_on(self):
        """Clear pass checkbox only"""

        cbox_statuses = self.checkbox.get_status()
        if cbox_statuses[self._index_pass]:
            self._toggle_visibility_checkbox(self._index_pass)

    def _toggle_visibility_checkbox(self, index):
        """toggles the visibility of a given checkbox"""

        l1, l2 = self.checkbox.lines[index]
        l1.set_visible(not l1.get_visible())
        l2.set_visible(not l2.get_visible())

    def get_ratings(self):
        """Returns the final set of checked ratings"""

        cbox_statuses = self.checkbox.get_status()
        user_ratings = [ cfg.t1_mri_default_issue_list[idx] for idx, this_cbox_active in enumerate(cbox_statuses) if this_cbox_active]

        return user_ratings

    def allowed_to_advance(self):
        """
        Method to ensure work is done for current iteration,
        before allowing the user to advance to next subject.

        Returns False if atleast one of the following conditions are not met:
            Atleast Checkbox is checked
        """

        if any(self.checkbox.get_status()):
            allowed = True
        else:
            allowed = False

        return allowed

    def reset_figure(self):
        "Resets the figure to prepare it for display of next subject."

        self.clear_data()
        self.clear_checkboxes()
        self.clear_notes_annot()

    def clear_data(self):
        """clearing all data/image handles"""

        if self.data_handles:
            for artist in self.data_handles:
                artist.remove()
            # resetting it
            self.data_handles = list()

    def clear_notes_annot(self):
        """clearing notes and annotations"""

        self.text_box.set_val(cfg.textbox_initial_text)
        # text is matplotlib artist
        self.annot_text.remove()

    def on_mouse(self, event):
        """Callback for mouse events."""

        if self.prev_axis is not None:
            # include all the non-data axes here (so they wont be zoomed-in)
            if event.inaxes not in [self.checkbox.ax, self.text_box.ax,
                                    self.bt_next.ax, self.bt_quit.ax]:
                self.prev_axis.set_position(self.prev_ax_pos)
                self.prev_axis.set_zorder(0)
                self.prev_axis.patch.set_alpha(0.5)
                self.zoomed_in = False

        # right click ignored
        if event.button in [3]:
            pass
        # double click to zoom in to any axis
        elif event.dblclick and event.inaxes is not None and \
            event.inaxes not in [self.checkbox.ax, self.text_box.ax,
                                 self.bt_next.ax, self.bt_quit.ax]:
            # zoom axes full-screen
            self.prev_ax_pos = event.inaxes.get_position()
            event.inaxes.set_position(cfg.zoomed_position)
            event.inaxes.set_zorder(1) # bring forth
            event.inaxes.set_facecolor('black') # black
            event.inaxes.patch.set_alpha(1.0)  # opaque
            self.zoomed_in = True
            self.prev_axis = event.inaxes

        else:
            pass

        plt.draw()

    def on_keyboard(self, key_in):
        """Callback to handle keyboard shortcuts to rate and advance."""

        # ignore keyboard key_in when mouse within Notes textbox
        if key_in.inaxes == self.text_box.ax or key_in.key is None:
            return

        key_pressed = key_in.key.lower()
        # print(key_pressed)
        if key_pressed in ['right', ' ', 'space']:
            self.next_button_callback()
        if key_pressed in ['ctrl+q', 'q+ctrl']:
            self.quit_button_callback()
        else:
            if key_pressed in cfg.abbreviation_t1_mri_default_issue_list:
                checked_label = cfg.abbreviation_t1_mri_default_issue_list[key_pressed]
                self.checkbox.set_active(cfg.t1_mri_default_issue_list.index(checked_label))
            else:
                pass


class RatingWorkflowT1(BaseWorkflow):
    """
    Rating workflow without any overlay.
    """


    def __init__(self,
                 id_list,
                 in_dir,
                 out_dir,
                 issue_list,
                 mri_name, in_dir_type,
                 outlier_method, outlier_fraction,
                 outlier_feat_types, disable_outlier_detection,
                 prepare_first,
                 vis_type,
                 views, num_slices_per_view, num_rows_per_view):
        """Constructor"""

        super().__init__(id_list, in_dir, out_dir,
                         outlier_method, outlier_fraction,
                         outlier_feat_types, disable_outlier_detection)

        self.vis_type = vis_type
        self.issue_list = issue_list
        self.mri_name = mri_name
        self.in_dir_type = in_dir_type
        self.expt_id = 'rate_mri_{}'.format(self.mri_name)
        self.suffix = self.expt_id
        self.current_alert_msg = None
        self.prepare_first = prepare_first

        self.init_layout(views, num_rows_per_view, num_slices_per_view)
        self.init_getters()

    def run(self):
        """Generate the required visualizations for the specified subjects."""

        self.preprocess()
        self.prepare_UI()
        self.loop_through_subjects()
        self.cleanup()

    def preprocess(self):
        """
        Preprocess the input data
            e.g. compute features, make complex visualizations etc.
            before starting the review process.
        """

        if not self.disable_outlier_detection:
            print('Preprocessing data - please wait .. '
                  '\n\t(or contemplate the vastness of universe! )')
            self.extract_features()
        self.detect_outliers()

        print('Restoring ratings from previous session(s), if they exist ..')
        self.restore_ratings()

        # no complex vis to generate - skipping

    def prepare_UI(self):
        """Main method to run the entire workflow"""

        self.open_figure()
        self.add_UI()
        self.add_histogram_panel()

    def init_layout(self, views, num_rows_per_view,
                    num_slices_per_view, padding=cfg.default_padding):

        self.views = views
        self.num_slices_per_view = num_slices_per_view
        self.num_rows_per_view = num_rows_per_view
        self.num_rows = len(self.views)*self.num_rows_per_view
        self.num_cols = int((len(self.views) * self.num_slices_per_view) / self.num_rows)
        self.padding = padding

    def init_getters(self):
        """Initializes the getters methods for input paths and feature readers."""

        from visualqc.features import extract_T1_features
        self.feature_extractor = extract_T1_features

        if self.vis_type is not None and (self.vis_type in cfg.freesurfer_vis_types or self.in_dir_type in ['freesurfer', ]):
            self.path_getter_inputs = lambda sub_id: realpath(pjoin(self.in_dir, sub_id, 'mri', self.mri_name))
        else:
            self.path_getter_inputs = lambda sub_id: realpath(pjoin(self.in_dir, sub_id, self.mri_name))

    def open_figure(self):
        """Creates the master figure to show everything in."""

        self.figsize = cfg.t1_mri_review_figsize
        plt.style.use('dark_background')
        self.fig, self.axes = plt.subplots(self.num_rows, self.num_cols, figsize=self.figsize)
        self.axes = self.axes.flatten()

        # vmin/vmax are controlled, because we rescale all to [0, 1]
        self.display_params = dict(interpolation='none', aspect='equal',
                              origin='lower', cmap='gray', vmin=0.0, vmax=1.0)

        # turning off axes, creating image objects
        self.images = [None] * len(self.axes)
        empty_image = np.full((10,10), 0.0)
        for ix, ax in enumerate(self.axes):
            ax.axis('off')
            self.images[ix] = ax.imshow(empty_image, **self.display_params)

        # leaving some space on the right for review elements
        plt.subplots_adjust(**cfg.review_area)
        plt.show(block=False)

    def restore_ratings(self):
        """Restores any ratings from previous sessions."""

        from visualqc.utils import restore_previous_ratings
        self.ratings, self.notes, self.incomplete_list = restore_previous_ratings(self)

    def save_ratings(self):
        """Saves ratings to disk """

        print('Saving ratings .. \n')
        ratings_file, prev_ratings_backup = get_ratings_path_info(self)

        if pexists(ratings_file):
            copyfile(ratings_file, prev_ratings_backup)

        # add column names: subject_id,issue1:issue2:issue3,...,notes etc
        lines = '\n'.join(['{},{},{}'.format(sid, _plus_join(rating_set), self.notes[sid]) for sid, rating_set in self.ratings.items()])
        try:
            with open(ratings_file, 'w') as cf:
                cf.write(lines)
        except:
            raise IOError(
                'Error in saving ratings to file!!\n'
                'Backup might be helpful at:\n\t{}'.format(prev_ratings_backup))

    def add_UI(self):
        """Adds the review UI with defaults"""

        self.UI = T1MriInterface(self.fig, self.axes, self.issue_list, self.next, self.quit)

        # connecting callbacks
        self.con_id_click = self.fig.canvas.mpl_connect('button_press_event', self.UI.on_mouse)
        self.con_id_keybd = self.fig.canvas.mpl_connect('key_press_event', self.UI.on_keyboard)
        # con_id_scroll = self.fig.canvas.mpl_connect('scroll_event', self.UI.on_scroll)

        self.fig.set_size_inches(self.figsize)

    def add_histogram_panel(self):
        """Extra axis for histogram"""

        self.ax_hist = plt.axes(cfg.position_histogram_t1_mri)
        self.ax_hist.set_xticks(cfg.xticks_histogram_t1_mri)
        self.ax_hist.set_yticks([])
        self.ax_hist.set_autoscaley_on(True)
        self.ax_hist.set_prop_cycle('color', cfg.color_histogram_t1_mri)
        self.ax_hist.set_title(cfg.title_histogram_t1_mri, fontsize='small')

    def update_histogram(self, img):
        """Updates histogram with current image data"""

        nonzero_values = img.ravel()[np.flatnonzero(img)]
        _, _, patches_hist = self.ax_hist.hist(nonzero_values, density=True, bins=cfg.num_bins_histogram_display)
        self.ax_hist.relim(visible_only=True)
        self.ax_hist.autoscale_view(scalex=False) # xlim fixed to [0, 1]
        self.UI.data_handles.extend(patches_hist)

    def update_alerts(self):
        """Keeps a box, initially invisible."""

        if self.current_alert_msg is not None:
            h_alert_text= self.fig.text(cfg.position_outlier_alert[0], cfg.position_outlier_alert[1],
                                         self.current_alert_msg, **cfg.alert_text_props)
            # adding it to list of elements to cleared when advancing to next subject
            self.UI.data_handles.append(h_alert_text)

    def add_alerts(self):
        """Brings up an alert if subject id is detected to be an outlier."""

        flagged_as_outlier = self.current_subject_id in self.by_sample
        if flagged_as_outlier:
            alerts_list = self.by_sample.get(self.current_subject_id, None)  # None, if id not in dict
            print('\n\tFlagged as a possible outlier by these measures:\n\t\t{}'.format('\t'.join(alerts_list)))

            strings_to_show = ['Flagged as an outlier:', ] + alerts_list
            self.current_alert_msg = '\n'.join(strings_to_show)
            self.update_alerts()
        else:
            self.current_alert_msg = None


    def loop_through_subjects(self):
        """Workhorse for the workflow!"""

        for subject_id in self.incomplete_list:

            print('\nReviewing {}'.format(subject_id))
            self.current_subject_id = subject_id
            self.UI.add_annot(subject_id)
            self.add_alerts()

            t1_mri, out_path, skip_subject = self.load_data(subject_id)

            if skip_subject:
                print('Skipping current subject ..')
                continue

            self.display_data(t1_mri)

            # informing only when it was rated!
            if self.ratings[subject_id] not in cfg.ratings_not_to_be_recorded:
                print('id {} issues {} notes {}'.format(subject_id, _plus_join(self.ratings[subject_id]),
                                                        self.notes[subject_id]))
            else:
                self.ratings.pop(subject_id)

            if self.quit_now:
                print('\nUser chosen to quit..')
                break

    def quit(self, input_event_to_ignore=None):
        "terminator"

        if self.UI.allowed_to_advance():
            self.prepare_to_advance()
            self.quit_now = True
        else:
            print('You have not rated the current subject! '
                  'Please rate it before you can advance '
                  'to next subject, or to quit..')

    def next(self, input_event_to_ignore=None):
        "advancer"

        if self.UI.allowed_to_advance():
            self.prepare_to_advance()
            self.quit_now = False
        else:
            print('You have not rated the current subject! '
                  'Please rate it before you can advance '
                  'to next subject, or to quit..')

    def prepare_to_advance(self):
        """Work needed before moving to next subject"""

        self.capture_user_input()
        self.UI.reset_figure()
        # stopping the blocking event loop
        self.fig.canvas.stop_event_loop()

    def capture_user_input(self):
        """Updates all user input to class"""

        self.ratings[self.current_subject_id] = self.UI.get_ratings()
        self.notes[self.current_subject_id] = self.UI.user_notes

    def load_data(self, subject_id):
        """Loads the image data for display."""

        t1_mri_path = self.path_getter_inputs(subject_id)
        t1_mri = read_image(t1_mri_path, error_msg='T1 mri')

        skip_subject = False
        if np.count_nonzero(t1_mri)==0:
            skip_subject = True
            print('MR image is empty!')
            out_vis_path = None
        else:
            # where to save the visualization to
            out_vis_path = pjoin(self.out_dir, 'visual_qc_{}_{}'.format(self.vis_type, subject_id))

        return t1_mri, out_vis_path, skip_subject

    def display_data(self, img):
        """Adds slice collage to the given axes"""

        # crop and rescale
        img = crop_image(img, self.padding)
        img = scale_0to1(img)

        # adding slices
        slices = pick_slices(img, self.views, self.num_slices_per_view)
        for ax_index, (dim_index, slice_index) in enumerate(slices):
            slice_data = get_axis(img, dim_index, slice_index)
            self.images[ax_index].set_data(slice_data)
            # im_handle = self.axes[ax_index].imshow(slice_data, **self.display_params)
            # self.UI.data_handles.append(im_handle)

        # updating histogram
        self.update_histogram(img)

        # window management
        self.fig.canvas.manager.show()
        self.fig.canvas.draw_idle()
        # starting a 'blocking' loop to let the user interact
        self.fig.canvas.start_event_loop(timeout=-1)

        return

    def cleanup(self):
        """Preparating for exit."""

        # save ratings before exiting
        self.save_ratings()

        self.fig.canvas.mpl_disconnect(self.con_id_click)
        self.fig.canvas.mpl_disconnect(self.con_id_keybd)
        plt.close('all')


def get_parser():
    """Parser to specify arguments and their defaults."""

    parser = argparse.ArgumentParser(prog="T1_mri_visualqc",
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     description='T1_mri_visualqc: rate quality of anatomical MR scan.')

    help_text_fs_dir = textwrap.dedent("""
    Absolute path to ``SUBJECTS_DIR`` containing the finished runs of Freesurfer parcellation
    Each subject will be queried after its ID in the metadata file.

    E.g. ``--fs_dir /project/freesurfer_v5.3``
    \n""")

    help_text_user_dir = textwrap.dedent("""
    Absolute path to an input folder containing the MRI scan. 
    Each subject will be queried after its ID in the metadata file, 
    and is expected to have the MRI (specified ``--mri_name``), 
    in its own folder under --user_dir.

    E.g. ``--user_dir /project/images_to_QC``
    \n""")

    help_text_id_list = textwrap.dedent("""
    Abs path to file containing list of subject IDs to be processed.
    If not provided, all the subjects with required files will be processed.

    E.g.

    .. parsed-literal::

        sub001
        sub002
        cn_003
        cn_004

    \n""")

    help_text_mri_name = textwrap.dedent("""
    Specifies the name of MRI image to serve as the reference slice.
    Typical options include orig.mgz, brainmask.mgz, T1.mgz etc.
    Make sure to choose the right vis_type.

    Default: {} (within the mri folder of Freesurfer format).
    \n""".format(cfg.default_mri_name))

    help_text_out_dir = textwrap.dedent("""
    Output folder to store the visualizations & ratings.
    Default: a new folder called ``{}`` will be created inside the ``fs_dir``
    \n""".format(cfg.default_out_dir_name))

    help_text_views = textwrap.dedent("""
    Specifies the set of views to display - could be just 1 view, or 2 or all 3.
    Example: --views 0 (typically sagittal) or --views 1 2 (axial and coronal)
    Default: {} {} {} (show all the views in the selected segmentation)
    \n""".format(cfg.default_views[0], cfg.default_views[1], cfg.default_views[2]))

    help_text_num_slices = textwrap.dedent("""
    Specifies the number of slices to display per each view. 
    This must be even to facilitate better division.
    Default: {}.
    \n""".format(cfg.default_num_slices))

    help_text_num_rows = textwrap.dedent("""
    Specifies the number of rows to display per each axis. 
    Default: {}.
    \n""".format(cfg.default_num_rows))

    help_text_prepare = textwrap.dedent("""
    This flag enables batch-generation of 3d surface visualizations, prior to starting any review and rating operations. 
    This makes the switch from one subject to the next, even more seamless (saving few seconds :) ).

    Default: False (required visualizations are generated only on demand, which can take 5-10 seconds for each subject).
    \n""")

    help_text_outlier_detection_method = textwrap.dedent("""
    Method used to detect the outliers.

    For more info, read http://scikit-learn.org/stable/modules/outlier_detection.html

    Default: {}.
    \n""".format(cfg.default_outlier_detection_method))

    help_text_outlier_fraction = textwrap.dedent("""
    Fraction of outliers expected in the given sample. Must be >= 1/n and <= (n-1)/n, 
    where n is the number of samples in the current sample.

    For more info, read http://scikit-learn.org/stable/modules/outlier_detection.html

    Default: {}.
    \n""".format(cfg.default_outlier_fraction))

    help_text_outlier_feat_types = textwrap.dedent("""
    Type of features to be employed in training the outlier detection method.  It could be one of  
    'cortical' (aparc.stats: mean thickness and other geometrical features from each cortical label), 
    'subcortical' (aseg.stats: volumes of several subcortical structures), 
    or 'both' (using both aseg and aparc stats).

    Default: {}.
    \n""".format(cfg.t1_mri_features_OLD))

    help_text_disable_outlier_detection = textwrap.dedent("""
    This flag disables outlier detection and alerts altogether.
    \n""")

    in_out = parser.add_argument_group('Input and output', ' ')

    in_out.add_argument("-i", "--id_list", action="store", dest="id_list",
                        default=None, required=False, help=help_text_id_list)

    in_out.add_argument("-u", "--user_dir", action="store", dest="user_dir",
                        default=cfg.default_user_dir,
                        required=False, help=help_text_user_dir)

    in_out.add_argument("-m", "--mri_name", action="store", dest="mri_name",
                             default=cfg.default_mri_name, required=False,
                             help=help_text_mri_name)

    in_out.add_argument("-o", "--out_dir", action="store", dest="out_dir",
                        required=False, help=help_text_out_dir,
                        default=None)

    in_out.add_argument("-f", "--fs_dir", action="store", dest="fs_dir",
                        default=cfg.default_freesurfer_dir,
                        required=False, help=help_text_fs_dir)
    outliers = parser.add_argument_group('Outlier detection',
                                         'options related to automatically detecting possible outliers')
    outliers.add_argument("-olm", "--outlier_method", action="store", dest="outlier_method",
                          default=cfg.default_outlier_detection_method, required=False,
                          help=help_text_outlier_detection_method)

    outliers.add_argument("-olf", "--outlier_fraction", action="store", dest="outlier_fraction",
                          default=cfg.default_outlier_fraction, required=False,
                          help=help_text_outlier_fraction)

    outliers.add_argument("-olt", "--outlier_feat_types", action="store", dest="outlier_feat_types",
                          default=cfg.t1_mri_features_OLD, required=False,
                          help=help_text_outlier_feat_types)

    outliers.add_argument("-old", "--disable_outlier_detection", action="store_true",
                          dest="disable_outlier_detection",
                          required=False, help=help_text_disable_outlier_detection)

    layout = parser.add_argument_group('Layout options', ' ')
    layout.add_argument("-w", "--views", action="store", dest="views",
                        default=cfg.default_views, required=False, nargs='+',
                        help=help_text_views)

    layout.add_argument("-s", "--num_slices", action="store", dest="num_slices",
                        default=cfg.default_num_slices, required=False,
                        help=help_text_num_slices)

    layout.add_argument("-r", "--num_rows", action="store", dest="num_rows",
                        default=cfg.default_num_rows, required=False,
                        help=help_text_num_rows)

    wf_args = parser.add_argument_group('Workflow', 'Options related to workflow '
                                                    'e.g. to pre-compute resource-intensive features, '
                                                    'and pre-generate all the visualizations required')
    wf_args.add_argument("-p", "--prepare_first", action="store_true", dest="prepare_first",
                         help=help_text_prepare)

    return parser


def make_workflow_from_user_options():
    """Parser/validator for the cmd line args."""

    parser = get_parser()

    if len(sys.argv) < 2:
        print('Too few arguments!')
        parser.print_help()
        parser.exit(1)

    # parsing
    try:
        user_args = parser.parse_args()
    except:
        parser.exit(1)

    vis_type = 'collage_t1_mri'
    type_of_features = 't1_mri'
    in_dir, in_dir_type = check_input_dir_T1(user_args.fs_dir, user_args.user_dir)

    mri_name = user_args.mri_name
    id_list, images_for_id = check_id_list(user_args.id_list, in_dir, vis_type,
                                           mri_name, seg_name=None,
                                           in_dir_type=in_dir_type)

    out_dir = check_out_dir(user_args.out_dir, in_dir)
    views = check_views(user_args.views)

    num_slices_per_view, num_rows_per_view = check_finite_int(user_args.num_slices, user_args.num_rows)

    outlier_method, outlier_fraction, \
    outlier_feat_types, disable_outlier_detection = check_outlier_params(user_args.outlier_method,
                                                                         user_args.outlier_fraction,
                                                                         user_args.outlier_feat_types,
                                                                         user_args.disable_outlier_detection,
                                                                         id_list, vis_type, type_of_features)

    wf = RatingWorkflowT1(id_list, in_dir, out_dir,
                          cfg.t1_mri_default_issue_list,
                          mri_name, in_dir_type,
                          outlier_method, outlier_fraction,
                          outlier_feat_types, disable_outlier_detection,
                          user_args.prepare_first,
                          vis_type,
                          views, num_slices_per_view, num_rows_per_view)

    return wf


def cli_run():
    """Main entry point."""

    wf = make_workflow_from_user_options()

    if wf.vis_type is not None:
        # matplotlib.interactive(True)
        wf.run()
        print('Results are available in:\n\t{}'.format(wf.out_dir))
    else:
        raise ValueError('Invalid state for visualQC!\n'
                         '\t Ensure proper combination of arguments is used.')

    return


if __name__ == '__main__':
    # disabling all not severe warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)

        cli_run()

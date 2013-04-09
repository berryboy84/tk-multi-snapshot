"""
Copyright (c) 2013 Shotgun Software, Inc
----------------------------------------------------
"""
import os
from datetime import datetime 
import tempfile

import tank
from tank import TankError
from tank.platform.qt import QtCore, QtGui
from tank_vendor import yaml

class Snapshot(object):
    """
    Main snapshot handler
    """
    
    # Format of the timestamp used in snapshot files
    TIMESTAMP_FMT = "%Y-%m-%d-%H-%M-%S"
    
    @staticmethod
    def show_snapshot_dlg(app):
        """
        Helper method to do a snapshot with a dialog
        """
        handler = Snapshot(app)
        handler._show_snapshot_dlg()
        
    @staticmethod
    def show_snapshot_history_dlg(app):
        """
        Helper method to show the snapshot history dialog
        """
        handler = Snapshot(app)
        handler._show_snapshot_history_dlg()
        
    def __init__(self, app):
        """
        Construction
        """
        self._app = app
        
        self._snapshot_history_ui = None
        
        self._work_template = self._app.get_template("template_work")
        self._snapshot_template = self._app.get_template("template_snapshot")
        
    def save_current_file(self):
        """
        Use hook to save the current work/scene file
        """
        self._app.execute_hook("hook_scene_operation", operation="save", file_path="")
        
    def get_current_file_path(self):
        """
        Use hook to get the current work/scene file path
        """
        return self._app.execute_hook("hook_scene_operation", operation="current_path", file_path="")

    def open_file(self, file_path):
        """
        Use hook to open the specified file
        """
        self._app.execute_hook("hook_scene_operation", operation="open", file_path=file_path)
        
    def copy_file(self, source_path, target_path):
        """
        Use hook to copy source file to target path
        """
        self._app.execute_hook("hook_copy_file", source_path=source_path, target_path=target_path)
    
    def do_snapshot(self, work_path, thumbnail, comment):
        """
        Do a snapshot using the specified details
        """
        
        # save the current scene:
        self.save_current_file()
         
        # ensure work file exists:
        if not os.path.exists(work_path):
            raise TankError("Snapshot: Work file %s could not be found on disk!" % work_path)
        
        # validate work file:
        if not self._work_template.validate(work_path):
            raise TankError("Unable to snapshot non-work file %s" % work_path)
        
        # use work file to find fields for snapshot:
        fields = self._work_template.get_fields(work_path)
        
        # add additional fields:
        fields["timestamp"] = datetime.now().strftime(Snapshot.TIMESTAMP_FMT)
        
        if "increment" in self._snapshot_template.keys:
            # work out next increment from existing snapshots:
            fields["increment"] = self._find_next_snapshot_increment({})
        
        # generate snapshot path:
        snapshot_path = self._snapshot_template.apply_fields(fields)
        
        # copy file via hook:
        self._app.log_debug("Snapshot: Copying %s --> %s" % (work_path, snapshot_path))
        self.copy_file(work_path, snapshot_path)

        # make sure snapshot exists:
        if not os.path.exists(snapshot_path):
            raise TankError("Snapshot: Failed to copy work file from '%s' to '%s'" 
                            % (work_path, snapshot_path))
        
        # ok, snapshot succeeded so update comment and thumbnail if we have them:
        if comment:
            self._add_snapshot_comment(snapshot_path, comment)
        if thumbnail:
            self._add_snapshot_thumbnail(snapshot_path, thumbnail)
        
    def restore_snapshot(self, snapshot_path):
        """
        Restore snapshot from the specified path
        """
        if not snapshot_path:
            return
        
        # to be on the safe side, save the current file
        # as it may be overidden:
        self.save_current_file()
        
        # validate snapshot path
        if not self._snapshot_template.validate(snapshot_path):
            raise TankError("%s is not a valid snapshot path!" % snapshot_path)
        fields = self._snapshot_template.get_fields(snapshot_path)
        work_path = self._work_template.apply_fields(fields)
        
        # check to see if work file exists and if it does, snapshot it first:
        if os.path.exists(work_path):
            try:
                comment = ("Automatic snapshot before restoring older snapshot '%s'" 
                            % os.path.basename(snapshot_path))
                self.do_snapshot(work_path, None, comment)
            except:
                # reformat error?
                raise
        
        # now use hook to copy snapshot back to work path:
        self._app.log_debug("Snapshot Restore: Copying %s --> %s" % (work_path, snapshot_path))
        self.copy_file(snapshot_path, work_path)
        
        # finally, use hook to re-open work file:
        self._app.log_debug("Snapshot Restore: Opening %s" % (work_path))
        self.open_file(work_path)

    def find_snapshot_history(self, file_path):
        """
        Find snapshot history for specified file
        """
        history = []
        if not file_path:
            return history
        
        # get fields using the work template:
        fields = []
        if self._work_template.validate(file_path):
            fields = self._work_template.get_fields(file_path)
        elif self._snapshot_template.validate(file_path):
            fields = self._snapshot_template.get_fields(file_path)
        else:
            # not a valid work file or snapshot!
            return history
        
        # find files that match the snapshot template ignoring certain fields:
        files = self._app.tank.paths_from_template(self._snapshot_template, 
                                             fields, 
                                             ["version", "timestamp", "increment"])
        if len(files) == 0:
            return history 
        
        # load comments & thumbnails and build history:
        comments = self._get_snapshot_comments(files[0])
        
        for file in files:
            details = {"file":file, 
                       "comment":comments.get(os.path.basename(file), ""), 
                       "thumbnail_path":self._get_thumbnail_file_path(file)
                       }
            
            # add additional details if we have then:
            fields = self._snapshot_template.get_fields(file)
            
            for key_name in ["version", "increment"]:
                if key_name in fields.keys():
                    details[key_name] = fields[key_name]

            timestamp = fields.get("timestamp")
            if timestamp:
                details["datetime"] = datetime.strptime(timestamp, Snapshot.TIMESTAMP_FMT)
                 
            # user?
            
            history.append(details)
            
        return history 

    def _show_snapshot_dlg(self):
        """
        Perform a snapshot of the current work file with the help of the UI
        """
      
        # save current work file and get path:
        try:
            work_file_path = self.get_current_file_path()
        except Exception, e:
            msg = ("Failed to save the current work file due to the following reason:\n\n"
                  "    %s\n\n"
                  "Unable to continue!" % e)
            QtGui.QMessageBox.critical(None, "Snapshot Error!", msg)
            return
        
        # current scene path must match work template and contain version:
        if not self._work_template.validate(work_file_path):
            # (AD) - need to hook this up to workfiles
            msg = ("Current scene is not a valid work file!  Please save as a\n"
                   "work file to continue")
            QtGui.QMessageBox.critical(None, "Unable To Snapshot!", msg)
            return
        
        # get initial thumbnail if there is one:
        thumbnail = QtGui.QPixmap(self._app.execute_hook("hook_thumbnail"))
        
        # show snapshot dialog as modal dialog:
        from .snapshot_form import SnapshotForm
        title = self._app.get_setting("snapshot_display_name")
        (res, snapshot_widget) = self._app.engine.show_modal(title, self._app, SnapshotForm, work_file_path, thumbnail, self._setup_snapshot_ui)
      
        # special case return code to show history dialog:
        if res == SnapshotForm.SHOW_HISTORY_RETURN_CODE:
            self._show_snapshot_history_dlg()

        
    def _setup_snapshot_ui(self, snapshot_widget):
        """
        Called during snapshot dialog creation to give us a
        chance to hook up signals etc.
        """
        snapshot_widget.snapshot.connect(self._do_snapshot_from_ui)
        
    def _do_snapshot_from_ui(self, snapshot_widget, file_path):
        """
        Triggered when user clicks 'Create Snapshot' button
        in the UI
        """
        # get data from widget:
        thumbnail = snapshot_widget.thumbnail
        comment = snapshot_widget.comment
        
        # try to do the snapshot
        status = True
        msg = ""
        try:
            self.do_snapshot(file_path, thumbnail, comment)
        except Exception, e:
            status = False
            msg = "%s" % e
            
        # update UI:
        snapshot_widget.show_result(status, msg)

    def _show_snapshot_history_dlg(self):
        """
        Show the snapshot history UI for the current path
        """
        title = self._app.get_setting("snapshot_history_display_name")
        
        # create dialog and hook up signals:
        from .snapshot_history_form import SnapshotHistoryForm
        self._snapshot_history_ui = self._app.engine.show_dialog(title, self._app, SnapshotHistoryForm, self._app, self)
        
        self._snapshot_history_ui.restore.connect(self._on_history_restore_snapshot)
        self._snapshot_history_ui.snapshot.connect(self._on_history_do_snapshot)
        
        # update UI:
        self._update_snapshot_history_ui()
    
    def _update_snapshot_history_ui(self):
        """
        Update the snapshot history UI after a change
        """
        # get the current file path:
        current_file_path = None
        try:
            current_file_path = self.get_current_file_path()
        except Exception, e:
            msg = ("Failed to find the current work file path due to the following reason:\n\n"
                  "%s\n\n"
                  "Unable to continue!" % e)
            QtGui.QMessageBox.critical(None, "Snapshot History Error!", msg)
            current_file_path = None
            
        self._snapshot_history_ui.path = current_file_path

    def _on_history_restore_snapshot(self, snapshot_path):
        """
        Restore the specified snapshot
        """
        try:
            self.restore_snapshot(snapshot_path)
        except:
            raise
        
        self._snapshot_history_ui.refresh()
        
    def _on_history_do_snapshot(self):
        """
        Switch to the snapshot UI from the history UI
        """        
        # hide the snapshot history window:
        self._snapshot_history_ui.window().hide()
        
        # do a snapshot:
        self._show_snapshot_dlg()

        # show history windows and refresh:
        self._snapshot_history_ui.window().show()        
        self._snapshot_history_ui.refresh()

    def _find_next_snapshot_increment(self, snapshot_fields):
        # work out the snapshot directory and find all files
        
        # next, re-construct the work-file for each snapshot
        
        # match against the work_file we have
        
        # for all matching, find highest 'increment' number
        pass
        
    def _add_snapshot_thumbnail(self, snapshot_file_path, thumbnail):
        """
        Save a thumbnail for the specified snapshot file path
        """
        if not thumbnail or thumbnail.isNull():
            return
        
        # write out to tmp path:
        temp_file = tempfile.NamedTemporaryFile(suffix=".png", prefix="tanktmp", delete=False)
        temp_path = temp_file.name
        temp_file.close()
        
        try:
            if not thumbnail.save(temp_path, "PNG"):
                raise TankError("Snapshot: Failed to save thumbnail to '%s'" % temp_path)
            
            # work out actual path:
            thumbnail_path = self._get_thumbnail_file_path(snapshot_file_path)
            
            # finally, use hook to copy:
            self._app.log_debug("Snapshot: Copying %s --> %s" % (temp_path, thumbnail_path))
            self.copy_file(temp_path, thumbnail_path)
        finally:
            os.remove(temp_path)

    def _get_thumbnail_file_path(self, snapshot_file_path):
        """
        Return path to snapshot thumbnail.  File path will be:

            <snapshot_dir>/<work_file_v0_name>.tank_thumb.png
            
        """
        thumbnail_path = "%s.tank_thumb.png" % os.path.splitext(snapshot_file_path)[0]
        return thumbnail_path
        
    def _get_comments_file_path(self, snapshot_file_path):
        """
        Snapshot comments file path will be:
        
            <snapshot_dir>/<work_file_v0_name>_comments.yml
            
        The assumption is that the snapshot template contains all fields
        required to reconstruct the work file - this has to be the case 
        though as otherwise we would never be able to restore a snapshot!
        """
        snapshot_dir = os.path.dirname(snapshot_file_path)
        fields = self._snapshot_template.get_fields(snapshot_file_path)
        
        # always save with version = 0 so that comments for all
        # versions are saved in the same file.
        fields["version"] = 0 
        
        work_path = self._work_template.apply_fields(fields)
        work_file_name = os.path.basename(work_path)
        work_file_title = os.path.splitext(work_file_name)[0]
        comments_file_path = "%s/%s.tank_comments.yml" % (snapshot_dir, work_file_title)
        
        return comments_file_path
    
        """
        (AD) - old version - relied on name in work file name
        # assume path where we store yml snapshots file is folder
        # where snapshots are stored (feels overkill to have a template
        # configured for this file)
        snapshot_dir = os.path.dirname(snapshot_file_path)
        fields = self._snapshot_template.get_fields(snapshot_file_path)
        comments_file_name = SNAPSHOT_COMMENTS_FILE % fields.get("name", "unknown")
        comments_file_path = os.path.join(snapshot_dir, comments_file_name)
        """
        
    def _add_snapshot_comment(self, snapshot_file_path, comment):
        """
        Added a comment to the comment file for a snapshot file.

        :param str file_path: path to the snapshot file.
        :param str comment: comment string to save.

        """
        # validate to make sure path is sane
        if not self._snapshot_template.validate(snapshot_file_path):
            self._app.log_warning("Could not add comment to "
                                         "invalid snapshot path %s!" % snapshot_file_path)
            return

        # get comments file path:        
        comments_file_path = self._get_comments_file_path(snapshot_file_path)
        self._app.log_debug("Snapshot: Adding comment to file %s" % comments_file_path)
        
        # load yml file
        comments = {}
        if os.path.exists(comments_file_path):
            comments = yaml.load(open(comments_file_path, "r"))
            
        # add entry for snapshot file:
        comments_key = os.path.basename(snapshot_file_path)
        """    
        (AD) - need to check but this makes no sense to me - surely just storing
        relative to the file name is a better idea or do we need to handle file
        names changing??
        
        # add entry - key it by name + timestamp + increment
        comments_key = (fields.get("name", "unknown"), 
                        fields.get("timestamp", "unknown"), 
                        fields.get("increment", "unknown"))
        """
        comments[comments_key] = comment
        
        # and save yml file
        yaml.dump(comments, open(comments_file_path, "w"))
        
    
    def _get_snapshot_comments(self, file_path):
        """
        Return the snapshot comments for the specified file path
        """
        comments_file_path = self._get_comments_file_path(file_path)
        comments = {}
        if os.path.exists(comments_file_path):
            comments = yaml.load(open(comments_file_path, "r"))
        return comments        
        
        
        
        
        
        